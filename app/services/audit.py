"""Аудит важных действий + простой in-memory rate-limit по IP.

Логируем только значимые события (вход, отправка кода, админка, заказы, оплата),
не каждый HTTP-запрос — чтобы при нагрузке/DDoS не топить БД записями.
Rate-limit держит счётчики в памяти процесса (1 web-контейнер — этого достаточно).
"""
import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent

logger = logging.getLogger(__name__)

AUDIT_RETENTION_DAYS = 30
_cleanup_marker = {"last": 0.0}   # чтобы чистить не чаще раза в час


def client_ip(request: Request) -> str:
    """Реальный IP клиента: X-Forwarded-For (за nginx) → прямой адрес."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


async def log_event(
    db: AsyncSession,
    request: Request,
    action: str,
    status: str = "ok",
    detail: str | None = None,
    user_id: int | None = None,
) -> None:
    """Пишет событие аудита. Ошибки логирования не должны ломать основной поток."""
    try:
        db.add(AuditEvent(
            ip=client_ip(request),
            action=action,
            status=status,
            detail=(detail or "")[:300] or None,
            user_id=user_id,
            path=str(request.url.path)[:200],
        ))
        await db.commit()
    except Exception as exc:
        logger.warning("[audit] не удалось записать событие %s: %s", action, exc)
        try:
            await db.rollback()
        except Exception:
            pass
    # периодическая чистка старого (не чаще раза в час)
    now = time.time()
    if now - _cleanup_marker["last"] > 3600:
        _cleanup_marker["last"] = now
        try:
            from datetime import datetime, timedelta
            cutoff = datetime.utcnow() - timedelta(days=AUDIT_RETENTION_DAYS)
            await db.execute(delete(AuditEvent).where(AuditEvent.created_at < cutoff))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass


# ─── In-memory rate-limit (скользящее окно по IP) ─────────────────────────────

_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(key: str, limit: int, window_sec: int) -> bool:
    """True, если для ключа (обычно "action:ip") превышен лимit за окно.
    Реализация — скользящее окно с очисткой устаревших отметок."""
    now = time.time()
    dq = _hits[key]
    # выбрасываем отметки старше окна
    cutoff = now - window_sec
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= limit:
        return True
    dq.append(now)
    # лёгкая защита от роста словаря: периодически чистим пустые очереди
    if len(_hits) > 10000:
        for k in [k for k, v in _hits.items() if not v]:
            _hits.pop(k, None)
    return False
