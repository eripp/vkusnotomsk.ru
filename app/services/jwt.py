from datetime import datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"


def create_access_token(data: dict[str, Any], days: int = 30) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(days=days)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
