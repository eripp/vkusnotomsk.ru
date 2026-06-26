"""Отправка уведомлений: email, MAX Business API, Telegram Bot."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Order, OrderItem, OrderStatus, NotificationSettings, User, OtpChannel,
)

logger = logging.getLogger(__name__)

# ─── Статусы для отображения ──────────────────────────────────────────────────

_STATUS_LABELS = {
    OrderStatus.new:      "Принят",
    OrderStatus.accepted: "Подтверждён",
    OrderStatus.delivery: "В пути",
    OrderStatus.done:     "Доставлен",
    OrderStatus.canceled: "Отменён",
}

_PAYMENT_LABELS = {
    "online":   "Онлайн",
    "terminal": "Терминал",
    "cash":     "Наличные",
}


# ─── OTP ──────────────────────────────────────────────────────────────────────

async def send_otp_code(phone: str, code: str, channel: OtpChannel) -> bool:
    """Доставляет код по выбранному каналу. Возвращает True, если код реально
    отправлен; False — для каналов-заглушек (MAX/VK) или при неудаче доставки.
    Для заглушек код показывается пользователю на экране (dev_code)."""
    text = f"Ваш код подтверждения Вкусно: {code}"

    if channel == OtpChannel.tg:
        logger.info("[OTP/TG] phone=%s", phone)
        # 1) Telegram Gateway (по номеру, без бота) — если настроен;
        # 2) иначе бот по сохранённому chat_id.
        if await send_telegram_gateway(phone, code):
            return True
        await _send_tg_by_phone(phone, text)
        return False

    if channel == OtpChannel.sms:
        logger.info("[OTP/SMS] phone=%s", phone)
        return await send_sms_ru(phone, text)

    # MAX / VK — заглушки: мессенджеры не умеют слать «холодный» код по номеру,
    # поэтому код не отправляется, а показывается на экране входа (dev_code).
    logger.info("[OTP/%s-stub] phone=%s code=%s", channel.value, phone, code)
    return False


async def send_sms_ru(phone: str, text: str) -> bool:
    """Отправка SMS через SMS.RU (https://sms.ru/sms/send).
    api_id — из настроек админки (приоритет), иначе из .env.
    Номер передаём без «+». Возвращает True, если SMS.RU принял сообщение
    (status_code == 100). При test-режиме сообщение не отправляется и баланс
    не списывается, но ответ имитирует успех."""
    from app.config import settings

    api_id = settings.SMSRU_API_ID
    test_mode = False
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        api_id = (cfg.get("smsru_api_id") or "").strip() or api_id
        test_mode = (cfg.get("smsru_test") or "") in ("1", "true", "on", "yes")
    except Exception as exc:
        logger.warning("[SMS.RU] не удалось прочитать настройки: %s", exc)

    if not api_id:
        logger.warning("[SMS.RU] нет api_id → %s: %s", phone, text)
        return False

    to = phone.lstrip("+")   # SMS.RU ждёт номер без «+», напр. 79991234567
    params = {"api_id": api_id, "to": to, "msg": text, "json": 1}
    if test_mode:
        params["test"] = 1

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://sms.ru/sms/send", params=params)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        logger.error("[SMS.RU] исключение: %s", exc)
        return False

    # Верхнеуровневый status_code: 100 — запрос принят. Иначе — ошибка авторизации/баланса.
    top = data.get("status_code")
    if top != 100:
        logger.warning("[SMS.RU] отказ status=%s code=%s text=%s",
                       data.get("status"), top, data.get("status_text"))
        return False

    # Статус по конкретному номеру.
    sms = (data.get("sms") or {}).get(to) or {}
    if sms.get("status_code") == 100:
        logger.info("[SMS.RU] отправлено %s (sms_id=%s, balance=%s)",
                    to, sms.get("sms_id"), data.get("balance"))
        return True

    logger.warning("[SMS.RU] номер %s не принят: code=%s text=%s",
                   to, sms.get("status_code"), sms.get("status_text"))
    return False


# ─── Публичные функции уведомлений ────────────────────────────────────────────

async def notify_order_new(db: AsyncSession, order: Order) -> None:
    """Новый заказ: клиенту + оператору в TG."""
    items = await _load_items(db, order.id)
    user  = await _load_user(db, order.user_id) if order.user_id else None

    # Клиенту
    if user:
        ns = await _get_ns(db, user.id)
        text = _order_sms_text(order, items, "Ваш заказ принят!")
        await _notify_user(user, ns, order, items, "Ваш заказ принят", text)

    # Оператору
    op_text = _operator_order_text(order, items)
    await _notify_operator(op_text)


async def notify_order_status(db: AsyncSession, order: Order) -> None:
    """Смена статуса заказа — уведомляем клиента."""
    if not order.user_id:
        return

    user = await _load_user(db, order.user_id)
    if not user:
        return

    ns    = await _get_ns(db, user.id)
    items = await _load_items(db, order.id)
    label = _STATUS_LABELS.get(order.status, order.status.value)

    subject = f"Заказ #{order.id}: {label}"
    text    = _order_sms_text(order, items, subject)
    await _notify_user(user, ns, order, items, subject, text)


async def notify_order_canceled(db: AsyncSession, order: Order, reason: str = "") -> None:
    """Отмена заказа."""
    if not order.user_id:
        return

    user = await _load_user(db, order.user_id)
    if not user:
        return

    ns   = await _get_ns(db, user.id)
    text = f"Заказ #{order.id} отменён."
    if reason:
        text += f" Причина: {reason}"

    if ns.max_orders and user.max_user_id:
        await send_max(user.max_user_id, text)
    if ns.tg_orders and user.tg_chat_id:
        await send_telegram(user.tg_chat_id, text)
    if ns.email_orders and user.email:
        html = _simple_email_html(f"Заказ #{order.id} отменён", text)
        await send_email(user.email, f"Заказ #{order.id} отменён — Вкусно Томск", html)


# ─── Низкоуровневые отправщики ────────────────────────────────────────────────

async def send_email(to: str, subject: str, html: str) -> None:
    from app.config import settings
    if not settings.SMTP_USER or not settings.SMTP_PASS:
        logger.warning("[EMAIL] нет SMTP-настроек, to=%s subject=%s", to, subject)
        return

    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Вкусно Томск <{settings.SMTP_USER}>"
        msg["To"]      = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        # Яндекс 465 → SSL; другие порты — STARTTLS
        use_tls = settings.SMTP_PORT == 465

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            use_tls=use_tls,
            start_tls=not use_tls,
            timeout=15,
        )
        logger.info("[EMAIL] отправлено to=%s subject=%s", to, subject)
    except Exception as exc:
        logger.error("[EMAIL] ошибка: %s", exc)


async def send_max(max_user_id: str | int, text: str) -> None:
    """Отправка сообщения через MAX Bot API.

    ВАЖНО: MAX НЕ умеет слать сообщение по номеру телефона — только по
    внутреннему user_id, который становится известен лишь после того, как
    пользователь сам написал боту (событие bot_started). Поэтому MAX пригоден
    для уведомлений уже привязанному юзеру, но НЕ для OTP по номеру.
    Реальный контракт (dev.max.ru): POST https://platform-api2.max.ru/messages
    ?user_id=<id>, заголовок Authorization: <token> (без Bearer)."""
    from app.config import settings
    if not settings.MAX_API_KEY:
        logger.warning("[MAX] нет ключа → user_id=%s: %s", max_user_id, text)
        return
    if not max_user_id:
        logger.warning("[MAX] нет user_id, отправка невозможна")
        return

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://platform-api2.max.ru/messages",
                params={"user_id": max_user_id},
                headers={"Authorization": settings.MAX_API_KEY},
                json={"text": text},
            )
            if resp.status_code >= 400:
                logger.warning("[MAX] ошибка %s: %s", resp.status_code, resp.text)
            else:
                logger.info("[MAX] отправлено user_id=%s", max_user_id)
    except Exception as exc:
        logger.error("[MAX] исключение: %s", exc)


async def send_telegram_gateway(phone: str, code: str) -> bool:
    """Отправляет код подтверждения через Telegram Gateway API
    (gatewayapi.telegram.org/sendVerificationMessage). Код генерируем мы и
    передаём его в Gateway — он только доставляет сообщение в Telegram по номеру.
    Возвращает True, если запрос принят Gateway."""
    from app.config import settings
    if not settings.TG_GATEWAY_TOKEN:
        return False

    # E.164: +7XXXXXXXXXX
    phone_e164 = phone if phone.startswith("+") else "+" + phone
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://gatewayapi.telegram.org/sendVerificationMessage",
                headers={"Authorization": f"Bearer {settings.TG_GATEWAY_TOKEN}"},
                json={"phone_number": phone_e164, "code": code, "ttl": 300},
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code < 400 and data.get("ok"):
                logger.info("[TG-Gateway] отправлено %s (request_id=%s)",
                            phone_e164, (data.get("result") or {}).get("request_id"))
                return True
            logger.warning("[TG-Gateway] ошибка %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.error("[TG-Gateway] исключение: %s", exc)
        return False


async def send_telegram(chat_id: int, text: str) -> None:
    from app.config import settings
    if not settings.TG_BOT_TOKEN:
        logger.warning("[TG] нет токена, chat_id=%s", chat_id)
        return

    try:
        import httpx
        url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            if resp.status_code >= 400:
                logger.warning("[TG] ошибка %s: %s", resp.status_code, resp.text[:200])
            else:
                logger.info("[TG] отправлено chat_id=%s", chat_id)
    except Exception as exc:
        logger.error("[TG] исключение: %s", exc)


# ─── Оператор ─────────────────────────────────────────────────────────────────

async def _notify_operator(text: str) -> None:
    """Уведомление оператору: TG (из переменной OPERATOR_TG_CHAT_ID)."""
    from app.config import settings
    chat_id = getattr(settings, "OPERATOR_TG_CHAT_ID", None)
    if not chat_id:
        logger.info("[OP] нет OPERATOR_TG_CHAT_ID: %s", text[:80])
        return
    await send_telegram(int(chat_id), text)


# ─── Вспомогательные ──────────────────────────────────────────────────────────

async def _notify_user(
    user: User,
    ns: NotificationSettings,
    order: Order,
    items: list[OrderItem],
    subject: str,
    sms_text: str,
) -> None:
    if ns.max_orders and user.max_user_id:
        await send_max(user.max_user_id, sms_text)
    if ns.tg_orders and user.tg_chat_id:
        await send_telegram(user.tg_chat_id, sms_text)
    if ns.email_orders and user.email:
        html = _order_email_html(order, items, subject)
        await send_email(user.email, f"{subject} — Вкусно Томск", html)


async def _load_items(db: AsyncSession, order_id: int) -> list[OrderItem]:
    res = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
    return res.scalars().all()


async def _load_user(db: AsyncSession, user_id: int) -> Optional[User]:
    res = await db.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


async def _get_ns(db: AsyncSession, user_id: int) -> NotificationSettings:
    res = await db.execute(
        select(NotificationSettings).where(NotificationSettings.user_id == user_id)
    )
    ns = res.scalar_one_or_none()
    if not ns:
        # дефолт: SMS и email включены
        ns = NotificationSettings(
            user_id=user_id,
            email_orders=True,
            max_orders=True,
            tg_orders=True,
        )
    return ns


async def _send_tg_by_phone(phone: str, text: str) -> None:
    """Telegram по номеру телефона: ищем tg_chat_id в БД."""
    # Реализация через Telegram login widget — у пользователя должен быть tg_chat_id
    # Пока логируем; привязка chat_id происходит через Telegram Login Widget (шаг 13+)
    logger.warning("[TG/PHONE] нет chat_id для %s, текст: %s", phone, text)


# ─── Шаблоны текстов ──────────────────────────────────────────────────────────

def _order_sms_text(order: Order, items: list[OrderItem], title: str) -> str:
    lines = [title, f"Заказ #{order.id}"]
    for it in items:
        lines.append(f"• {it.product_name} ×{it.quantity}")
    lines.append(f"Итого: {order.total_amount} ₽")
    lines.append(f"Доставка: {order.delivery_date} {order.slot_start}–{order.slot_end}")
    return "\n".join(lines)


def _operator_order_text(order: Order, items: list[OrderItem]) -> str:
    lines = [f"🛍 <b>Новый заказ #{order.id}</b>"]
    lines.append(f"📱 {order.phone}")
    lines.append(f"📍 {order.address}")
    lines.append(f"🗓 {order.delivery_date} {order.slot_start}–{order.slot_end}")
    lines.append(f"💳 {_PAYMENT_LABELS.get(order.payment_method.value, order.payment_method.value)}")
    lines.append("")
    for it in items:
        lines.append(f"• {it.product_name} ×{it.quantity} = {it.line_total} ₽")
    lines.append(f"\n<b>Итого: {order.total_amount} ₽</b>")
    if order.discount_amount:
        lines.append(f"Скидка: {order.discount_amount} ₽")
    if order.cashback_spent:
        lines.append(f"Бонусы: −{order.cashback_spent} ₽")
    return "\n".join(lines)


def _order_email_html(order: Order, items: list[OrderItem], title: str) -> str:
    from app.config import settings
    rows = "".join(
        f"<tr><td style='padding:4px 8px'>{it.product_name}</td>"
        f"<td style='padding:4px 8px;text-align:center'>{it.quantity}</td>"
        f"<td style='padding:4px 8px;text-align:right'>{it.line_total} ₽</td></tr>"
        for it in items
    )
    discount_row = (
        f"<tr><td colspan='2' style='padding:4px 8px'>Скидка</td>"
        f"<td style='padding:4px 8px;text-align:right;color:#e53'>−{order.discount_amount} ₽</td></tr>"
        if order.discount_amount else ""
    )
    cashback_row = (
        f"<tr><td colspan='2' style='padding:4px 8px'>Бонусы</td>"
        f"<td style='padding:4px 8px;text-align:right;color:#e53'>−{order.cashback_spent} ₽</td></tr>"
        if order.cashback_spent else ""
    )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:sans-serif;color:#222;max-width:560px;margin:0 auto;padding:20px">
  <h2 style="color:#ff6b2c">{title}</h2>
  <p>Заказ <strong>#{order.id}</strong></p>
  <table width="100%" cellspacing="0" style="border-collapse:collapse;margin:16px 0">
    <thead>
      <tr style="background:#f5f5f5">
        <th style="padding:6px 8px;text-align:left">Товар</th>
        <th style="padding:6px 8px">Кол-во</th>
        <th style="padding:6px 8px;text-align:right">Сумма</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
    <tfoot>
      {discount_row}{cashback_row}
      <tr style="font-weight:bold">
        <td colspan="2" style="padding:6px 8px;border-top:1px solid #ddd">Итого</td>
        <td style="padding:6px 8px;text-align:right;border-top:1px solid #ddd">{order.total_amount} ₽</td>
      </tr>
    </tfoot>
  </table>
  <p>📍 <strong>Адрес:</strong> {order.address}</p>
  <p>🗓 <strong>Доставка:</strong> {order.delivery_date}, {order.slot_start}–{order.slot_end}</p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="color:#888;font-size:13px">Вкусно Томск · <a href="{settings.SITE_URL}" style="color:#ff6b2c">{settings.SITE_URL}</a></p>
</body></html>"""


def _simple_email_html(title: str, body: str) -> str:
    from app.config import settings
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:sans-serif;color:#222;max-width:560px;margin:0 auto;padding:20px">
  <h2 style="color:#ff6b2c">{title}</h2>
  <p style="white-space:pre-line">{body}</p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="color:#888;font-size:13px">Вкусно Томск · <a href="{settings.SITE_URL}" style="color:#ff6b2c">{settings.SITE_URL}</a></p>
</body></html>"""
