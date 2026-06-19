import hashlib
import random
import string
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models import OtpCode, OtpChannel, User
from app.services.jwt import create_access_token, decode_access_token
from app.services.notifications import send_otp_code

router = APIRouter(tags=["auth"])

OTP_TTL_SECONDS = 300      # 5 минут
OTP_MAX_ATTEMPTS = 5
JWT_COOKIE = "vkusno_token"
JWT_TTL_DAYS = 30


# ─── Схемы ────────────────────────────────────────────────────────────────────

class SendOtpIn(BaseModel):
    phone: str
    channel: str = "max"   # "max" | "tg"


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
async def send_otp(payload: SendOtpIn, db: AsyncSession = Depends(get_db)):
    phone = _normalize_phone(payload.phone)

    try:
        channel = OtpChannel(payload.channel)
    except ValueError:
        channel = OtpChannel.max

    # инвалидируем старые коды для этого телефона
    await db.execute(
        update(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.used == False)
        .values(used=True)
    )

    code = _gen_code()
    expires = datetime.utcnow() + timedelta(seconds=OTP_TTL_SECONDS)

    otp = OtpCode(
        phone=phone,
        code_hash=_hash_code(code),
        channel=channel,
        expires_at=expires,
    )
    db.add(otp)
    await db.commit()

    # отправляем код (заглушка если ключей нет)
    await send_otp_code(phone=phone, code=code, channel=channel)

    return {"status": "sent", "expires_in": OTP_TTL_SECONDS}


@router.post("/verify-otp")
async def verify_otp(payload: VerifyOtpIn, response: Response, db: AsyncSession = Depends(get_db)):
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

    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Слишком много попыток, запросите новый код")

    if otp.code_hash != code_hash:
        otp.attempts += 1
        await db.commit()
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

    await db.commit()

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
