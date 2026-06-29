import hashlib
import random
import string
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models import OtpCode, OtpChannel, User
from app.services.jwt import create_access_token, decode_access_token
from app.services.notifications import send_otp_code
from app.services import idgtl
from app.services import captcha
from app.services import audit

router = APIRouter(tags=["auth"])

OTP_TTL_SECONDS = 300        # 5 минут жизни кода
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 60     # не чаще одного кода в 60с на номер (антифлуд)
JWT_COOKIE = "vkusno_token"
JWT_TTL_DAYS = 30

# Каналы, которые отправляются через i-dgtl (платный агрегатор). Пусто = i-dgtl
# не используется ни для одного канала. ВНИМАНИЕ: вход через Telegram идёт НЕ
# отсюда, а напрямую через TG Gateway в send_otp_code() (channel == tg) — поэтому
# OtpChannel.tg сюда добавлять НЕ нужно, иначе он уйдёт в i-dgtl вместо Gateway.
ENABLED_PAID_CHANNELS: set[OtpChannel] = set()


# ─── Схемы ────────────────────────────────────────────────────────────────────

class SendOtpIn(BaseModel):
    phone: str
    channel: str = "tg"   # "tg" (рабочий) | "sms" | "max"/"vk" (заглушки)
    captcha_token: str = ""   # токен Яндекс SmartCaptcha (если капча включена)


class VerifyOtpIn(BaseModel):
    phone: str
    code: str


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) != 11 or not digits.startswith("7"):
        raise HTTPException(status_code=400, detail="Неверный формат телефона")
    return "+" + digits


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _gen_code() -> str:
    return "".join(random.choices(string.digits, k=4))


# ─── Эндпоинты ────────────────────────────────────────────────────────────────

@router.post("/send-otp")
async def send_otp(payload: SendOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = audit.client_ip(request)

    # Rate-limit по IP: не больше 10 запросов кода за 5 минут с одного адреса.
    if audit.rate_limited(f"otp_send:{ip}", limit=10, window_sec=300):
        await audit.log_event(db, request, "otp_send", "blocked", "rate limit IP")
        raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")

    phone = _normalize_phone(payload.phone)

    # Антибот: проверяем токен SmartCaptcha (если капча настроена — иначе пропуск).
    if not await captcha.verify(payload.captcha_token, ip):
        await audit.log_event(db, request, "otp_send", "fail", "captcha")
        raise HTTPException(status_code=400, detail="Не пройдена проверка «я не робот». Обновите страницу.")

    try:
        channel = OtpChannel(payload.channel)
    except ValueError:
        channel = OtpChannel.tg

    # Антифлуд: не выдаём новый код, если последний по этому номеру создан
    # меньше OTP_RESEND_COOLDOWN секунд назад. id — последовательный, поэтому
    # берём самый свежий код и сравниваем его время выпуска (expires - TTL).
    last = (
        await db.execute(
            select(OtpCode)
            .where(OtpCode.phone == phone)
            .order_by(OtpCode.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is not None:
        issued_at = last.expires_at - timedelta(seconds=OTP_TTL_SECONDS)
        elapsed = (datetime.utcnow() - issued_at).total_seconds()
        if elapsed < OTP_RESEND_COOLDOWN:
            wait = int(OTP_RESEND_COOLDOWN - elapsed) + 1
            await audit.log_event(db, request, "otp_send", "blocked", f"cooldown {phone}")
            raise HTTPException(
                status_code=429,
                detail=f"Запросить новый код можно через {wait} с",
            )

    # инвалидируем старые коды для этого телефона
    await db.execute(
        update(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.used == False)
        .values(used=True)
    )
    expires = datetime.utcnow() + timedelta(seconds=OTP_TTL_SECONDS)

    # ── Путь 1: внешний верификатор i-dgtl (SMS / Telegram Gateway). Провайдер
    #    сам генерирует, шлёт и проверяет код — мы храним только его uuid.
    #    Работает только для каналов из ENABLED_PAID_CHANNELS (защита от затрат). ──
    if channel in ENABLED_PAID_CHANNELS and idgtl.is_supported(channel) and await idgtl.is_configured():
        uuid = await idgtl.idgtl_send(phone, channel)
        if uuid:
            otp = OtpCode(
                phone=phone,
                code_hash="",
                provider_uuid=uuid,
                channel=channel,
                expires_at=expires,
            )
            db.add(otp)
            await db.commit()
            await audit.log_event(db, request, "otp_send", "ok", f"{channel.value} {phone}")
            return {"status": "sent", "expires_in": OTP_TTL_SECONDS, "channel": channel.value}
        # i-dgtl настроен, но отправка не удалась — отдаём явную ошибку
        await audit.log_event(db, request, "otp_send", "fail", f"i-dgtl {phone}")
        raise HTTPException(status_code=502, detail="Не удалось отправить код. Попробуйте позже.")

    # ── Путь 2: код генерируем сами (каналы-заглушки MAX/VK либо i-dgtl не
    #    настроен). delivered=False → показываем код на экране (dev_code). ──
    code = _gen_code()
    otp = OtpCode(
        phone=phone,
        code_hash=_hash_code(code),
        channel=channel,
        expires_at=expires,
    )
    db.add(otp)
    await db.commit()

    delivered = await send_otp_code(phone=phone, code=code, channel=channel)
    await audit.log_event(db, request, "otp_send", "ok" if delivered else "fail",
                          f"{channel.value} {phone}{'' if delivered else ' (не доставлен)'}")

    resp = {"status": "sent", "expires_in": OTP_TTL_SECONDS, "channel": channel.value}
    if not delivered:
        resp["dev_code"] = code

    return resp


@router.post("/verify-otp")
async def verify_otp(payload: VerifyOtpIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = audit.client_ip(request)
    # Rate-limit перебора кода: не больше 20 проверок за 5 минут с одного IP.
    if audit.rate_limited(f"otp_verify:{ip}", limit=20, window_sec=300):
        await audit.log_event(db, request, "otp_verify", "blocked", "rate limit IP")
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")

    phone = _normalize_phone(payload.phone)
    code_hash = _hash_code(payload.code.strip())
    now = datetime.utcnow()

    # ищем актуальный код
    result = await db.execute(
        select(OtpCode)
        .where(
            OtpCode.phone == phone,
            OtpCode.used == False,
            OtpCode.expires_at > now,
        )
        .order_by(OtpCode.id.desc())
        .limit(1)
    )
    otp = result.scalar_one_or_none()

    if not otp:
        raise HTTPException(status_code=400, detail="Код устарел или не существует")

    # ── Проверка через i-dgtl: код проверяет сам провайдер по uuid сессии. ──
    if otp.provider_uuid:
        status = await idgtl.idgtl_check(otp.provider_uuid, payload.code.strip())
        if status == "CONFIRMED":
            otp.used = True
        elif status == "WRONG_CODE":
            await audit.log_event(db, request, "otp_verify", "fail", f"wrong code {phone}")
            raise HTTPException(status_code=400, detail="Неверный код")
        elif status in ("EXPIRED", "NOT_FOUND"):
            otp.used = True
            await db.commit()
            raise HTTPException(status_code=400, detail="Код устарел, запросите новый")
        else:
            raise HTTPException(status_code=502, detail="Не удалось проверить код. Попробуйте позже.")
    else:
        # ── Локальная проверка (каналы-заглушки / самосгенерированный код). ──
        if otp.attempts >= OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Слишком много попыток, запросите новый код")

        if otp.code_hash != code_hash:
            otp.attempts += 1
            await db.commit()
            await audit.log_event(db, request, "otp_verify", "fail", f"wrong code {phone}")
            left = OTP_MAX_ATTEMPTS - otp.attempts
            raise HTTPException(status_code=400, detail=f"Неверный код. Осталось попыток: {left}")

        # код верный — помечаем использованным
        otp.used = True

    # создаём/находим пользователя
    user_result = await db.execute(select(User).where(User.phone == phone))
    user = user_result.scalar_one_or_none()
    if not user:
        user = User(phone=phone)
        db.add(user)
        await db.flush()

    # телефон успешно подтверждён этим каналом
    user.phone_verified = True
    user.phone_verified_via = otp.channel.value

    await db.commit()
    await audit.log_event(db, request, "login", "ok", phone, user_id=user.id)

    token = create_access_token({"sub": str(user.id), "phone": phone}, days=JWT_TTL_DAYS)

    response.set_cookie(
        key=JWT_COOKIE,
        value=token,
        httponly=True,
        max_age=JWT_TTL_DAYS * 86400,
        samesite="lax",
        secure=False,   # True на проде с HTTPS
    )

    return {
        "status": "ok",
        "user": {"id": user.id, "phone": user.phone, "name": user.name},
    }


@router.get("/captcha-sitekey")
async def captcha_sitekey():
    """Клиентский ключ SmartCaptcha для рендера капчи (пустой → капча выключена)."""
    return {"sitekey": await captcha.get_sitekey()}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(JWT_COOKIE)
    return {"status": "ok"}


@router.get("/me")
async def me(
    vkusno_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not vkusno_token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    payload = decode_access_token(vkusno_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Токен недействителен")

    user_id = int(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or user.is_blocked:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    return {"id": user.id, "phone": user.phone, "name": user.name, "email": user.email}
