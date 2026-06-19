"""Telegram Bot webhook: /start <token> для привязки chat_id к пользователю."""
import hashlib
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User
from app.services.notifications import send_telegram

router = APIRouter(tags=["tgbot"])
logger = logging.getLogger(__name__)

_LINK_TTL = 600  # секунд — время жизни токена привязки (хранится в памяти процесса)
_pending: dict[str, int] = {}  # token → user_id (in-memory, достаточно для 1 воркера)


def make_link_token(user_id: int) -> str:
    """Создать одноразовый токен для привязки TG. Хранится в памяти."""
    import secrets, time
    token = secrets.token_urlsafe(16)
    _pending[token] = (user_id, time.time())
    return token


def _pop_token(token: str) -> int | None:
    import time
    entry = _pending.pop(token, None)
    if not entry:
        return None
    user_id, created = entry
    if time.time() - created > _LINK_TTL:
        return None
    return user_id


@router.post("/tg/webhook")
async def tg_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    from app.config import settings
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}

    message = data.get("message") or data.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text    = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            token = parts[1].strip()
            user_id = _pop_token(token)
            if user_id:
                result = await db.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    user.tg_chat_id = chat_id
                    await db.commit()
                    await send_telegram(chat_id, "✅ Telegram успешно привязан к вашему аккаунту Вкусно!")
                    logger.info("[TG] привязан chat_id=%s к user_id=%s", chat_id, user_id)
                    return {"ok": True}
            await send_telegram(chat_id, "Ссылка недействительна или устарела. Перейдите в личный кабинет и нажмите «Привязать Telegram» снова.")
        else:
            bot_username = settings.TG_BOT_USERNAME or "наш бот"
            await send_telegram(
                chat_id,
                f"Привет! Я бот Вкусно Томск.\n\nЧтобы получать уведомления о заказах, перейдите в <a href='{settings.SITE_URL}/account'>личный кабинет</a> и нажмите «Привязать Telegram».",
            )

    return {"ok": True}
