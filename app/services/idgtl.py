"""Интеграция с i-dgtl (direct.i-dgtl.ru) — модуль верификации телефона.

Провайдер сам генерирует код, доставляет его выбранным каналом (SMS /
Telegram Gateway и др.) и проверяет введённый код. Мы только пробрасываем
запросы и храним uuid сессии верификации между шагами send → check.

Контракт (api.docs.direct.i-dgtl.ru/verifier):
  POST /api/v1/verifier/send  {channelType, destination, gatewayId} → {uuid}
  POST /api/v1/verifier/check {uuid, code} → {status}
    status: CONFIRMED | WRONG_CODE | EXPIRED | NOT_FOUND
Авторизация: заголовок `Authorization: Basic <ключ из ЛК>` (ключ берём как есть).
"""
import logging

from app.config import settings
from app.models import OtpChannel

logger = logging.getLogger(__name__)

_BASE = "https://direct.i-dgtl.ru/api/v1/verifier"

# Наши каналы → channelType i-dgtl
_CHANNEL_MAP = {
    OtpChannel.sms: "SMS",
    OtpChannel.tg: "TELEGRAM_GATEWAY",
}


async def _credentials() -> tuple[str, str]:
    """api_key и gateway_id: из настроек админки (приоритет), иначе из .env."""
    api_key = settings.IDGTL_API_KEY
    gateway_id = settings.IDGTL_GATEWAY_ID
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        api_key = (cfg.get("idgtl_api_key") or "").strip() or api_key
        gateway_id = (cfg.get("idgtl_gateway_id") or "").strip() or gateway_id
    except Exception as exc:
        logger.warning("[i-dgtl] не удалось прочитать настройки: %s", exc)
    return api_key, gateway_id


def is_supported(channel: OtpChannel) -> bool:
    """Канал, который умеет обслуживать i-dgtl (SMS / Telegram Gateway)."""
    return channel in _CHANNEL_MAP


async def is_configured() -> bool:
    api_key, gateway_id = await _credentials()
    return bool(api_key and gateway_id)


async def idgtl_send(phone: str, channel: OtpChannel) -> str | None:
    """Просит i-dgtl сгенерировать и отправить код. Возвращает uuid сессии
    верификации или None при ошибке/не настроенных кредах."""
    api_key, gateway_id = await _credentials()
    if not api_key or not gateway_id:
        logger.warning("[i-dgtl] нет ключа/gatewayId — отправка невозможна")
        return None
    channel_type = _CHANNEL_MAP.get(channel)
    if not channel_type:
        logger.warning("[i-dgtl] канал %s не поддерживается", channel)
        return None

    destination = "".join(c for c in phone if c.isdigit())  # только цифры
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_BASE}/send",
                headers={"Authorization": f"Basic {api_key}",
                         "Content-Type": "application/json"},
                json={"channelType": channel_type,
                      "destination": destination,
                      "gatewayId": gateway_id},
            )
    except Exception as exc:
        logger.error("[i-dgtl] send исключение: %s", exc)
        return None

    if resp.status_code == 429:
        logger.warning("[i-dgtl] 429 — превышен лимит запросов на номер %s", destination)
        return None
    if resp.status_code >= 400:
        logger.warning("[i-dgtl] send ошибка %s: %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    uuid = data.get("uuid")
    if not uuid:
        logger.warning("[i-dgtl] send без uuid: %s", resp.text[:200])
        return None
    logger.info("[i-dgtl] код отправлен (%s) → %s, uuid=%s", channel_type, destination, uuid)
    return uuid


async def idgtl_check(uuid: str, code: str) -> str | None:
    """Проверяет код у i-dgtl. Возвращает status:
    CONFIRMED | WRONG_CODE | EXPIRED | NOT_FOUND, либо None при сетевой ошибке."""
    api_key, _ = await _credentials()
    if not api_key:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_BASE}/check",
                headers={"Authorization": f"Basic {api_key}",
                         "Content-Type": "application/json"},
                json={"uuid": uuid, "code": code},
            )
    except Exception as exc:
        logger.error("[i-dgtl] check исключение: %s", exc)
        return None

    if resp.status_code >= 400:
        logger.warning("[i-dgtl] check ошибка %s: %s", resp.status_code, resp.text[:200])
        return None
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return data.get("status")
