"""Отправка SMS через Plusofon (restapi.plusofon.ru).

Мы сами генерируем код, Plusofon только доставляет SMS (паттерн как у SMS.RU /
Telegram Gateway). Креды берём из настроек админки (приоритет), иначе из .env.

Контракт (help.plusofon.ru/api/v1/sms):
  POST https://restapi.plusofon.ru/api/v1/sms
  Headers: Client: <client_id>, Authorization: Bearer <token>,
           Content-Type/Accept: application/json
  Body: {text, number_id, to, reject_long, count_pdu}
  Ответ: {"data": {"id": "<jmx_id>"}, "success": true} | {"success": false, "message": "..."}
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_URL = "https://restapi.plusofon.ru/api/v1/sms"


async def _credentials() -> tuple[str, str, str]:
    """client_id, token, number_id: настройки админки (приоритет) → .env."""
    client_id = settings.PLUSOFON_CLIENT_ID
    token = settings.PLUSOFON_TOKEN
    number_id = settings.PLUSOFON_NUMBER_ID
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        client_id = (cfg.get("plusofon_client_id") or "").strip() or client_id
        token = (cfg.get("plusofon_token") or "").strip() or token
        number_id = (cfg.get("plusofon_number_id") or "").strip() or number_id
    except Exception as exc:
        logger.warning("[Plusofon] не удалось прочитать настройки: %s", exc)
    return client_id, token, number_id


async def is_configured() -> bool:
    client_id, token, number_id = await _credentials()
    return bool(client_id and token and number_id)


async def send_plusofon(phone: str, text: str) -> bool:
    """Отправляет SMS через Plusofon. Номер — цифры без «+» (79XXXXXXXXXX).
    Возвращает True, если запрос принят (success: true)."""
    client_id, token, number_id = await _credentials()
    if not (client_id and token and number_id):
        logger.warning("[Plusofon] нет кредов (client/token/number_id) → %s", phone)
        return False

    to = "".join(c for c in phone if c.isdigit())  # только цифры
    try:
        nid: int | str = int(number_id)
    except (TypeError, ValueError):
        nid = number_id

    payload = {
        "text": text,
        "number_id": nid,
        "to": to,
        "reject_long": True,   # не дробить длинные сообщения
        "count_pdu": True,
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _URL,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Client": str(client_id),
                    "Authorization": f"Bearer {token}",
                },
                json=payload,
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        logger.error("[Plusofon] исключение: %s", exc)
        return False

    if resp.status_code < 400 and data.get("success") is True:
        msg_id = (data.get("data") or {}).get("id")
        logger.info("[Plusofon] отправлено %s (id=%s)", to, msg_id)
        return True

    logger.warning("[Plusofon] отказ %s: status=%s success=%s msg=%s",
                   to, resp.status_code, data.get("success"), data.get("message"))
    return False
