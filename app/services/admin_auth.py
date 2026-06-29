"""Авторизация в админ-панель: bcrypt-пароли + подписанная сессия в cookie.

Доступ к /admin закрыт двумя уровнями:
  1) секретный префикс входа /admin/login/<ADMIN_URL_SECRET> (неугадываемый URL);
  2) логин/пароль (AdminUser, bcrypt).
Любой /admin/* без валидной сессии → 404 (админка полностью скрыта).
"""
import logging
import secrets

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AdminUser
from app.services.jwt import create_access_token, decode_access_token

logger = logging.getLogger(__name__)

ADMIN_COOKIE = "vkusno_admin"
ADMIN_SESSION_DAYS = 7


# ─── Пароли (bcrypt напрямую — passlib несовместим с bcrypt 5.x) ──────────────

def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:72]          # bcrypt — максимум 72 байта
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except Exception:
        return False


# ─── Сессия (переиспользуем JWT-подпись с маркером adm) ───────────────────────

def create_admin_session(admin_id: int) -> str:
    return create_access_token({"adm": admin_id}, days=ADMIN_SESSION_DAYS)


def decode_admin_session(token: str | None) -> int | None:
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or "adm" not in payload:
        return None
    try:
        return int(payload["adm"])
    except (TypeError, ValueError):
        return None


def secret_matches(value: str) -> bool:
    """Сравнение секрета входа в постоянное время. Пустой секрет → доступ закрыт."""
    if not settings.ADMIN_URL_SECRET:
        return False
    return secrets.compare_digest(value, settings.ADMIN_URL_SECRET)


# ─── Сидинг администратора из .env ────────────────────────────────────────────

async def seed_admin(db: AsyncSession) -> None:
    """Создаёт/обновляет AdminUser из ADMIN_USERNAME/ADMIN_PASSWORD при старте.
    Без ADMIN_PASSWORD ничего не делает (вход будет невозможен — это безопасно)."""
    if not settings.ADMIN_PASSWORD:
        logger.warning("[admin] ADMIN_PASSWORD не задан — вход в админку отключён")
        return
    username = settings.ADMIN_USERNAME or "admin"
    existing = (
        await db.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    new_hash = hash_password(settings.ADMIN_PASSWORD)
    if existing:
        existing.password_hash = new_hash
        existing.is_active = True
    else:
        db.add(AdminUser(username=username, password_hash=new_hash, is_active=True))
    await db.commit()
    logger.info("[admin] учётка '%s' готова", username)
