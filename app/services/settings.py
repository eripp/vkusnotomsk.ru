import time
from sqlalchemy import select

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # секунды


async def get_site_settings(db=None) -> dict:
    """Возвращает site_settings как dict с кешем 60с."""
    global _cache, _cache_ts
    if time.monotonic() - _cache_ts < _CACHE_TTL:
        return _cache
    if db is None:
        return _cache
    from app.models import SiteSetting
    result = await db.execute(select(SiteSetting))
    _cache = {row.key: row.value for row in result.scalars().all()}
    _cache_ts = time.monotonic()
    return _cache


def invalidate_settings_cache() -> None:
    global _cache_ts
    _cache_ts = 0.0
