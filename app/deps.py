from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User
from app.services.jwt import decode_access_token


async def get_current_user(
    vkusno_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not vkusno_token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    payload = decode_access_token(vkusno_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Токен недействителен")
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or user.is_blocked:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


async def get_optional_user(
    vkusno_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    if not vkusno_token:
        return None
    payload = decode_access_token(vkusno_token)
    if not payload:
        return None
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    return user if user and not user.is_blocked else None
