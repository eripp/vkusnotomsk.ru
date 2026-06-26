"""YooKassa payment integration (REST API v3)."""
import ipaddress
import json
import logging
import uuid

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3"


async def _credentials() -> tuple[str, str]:
    """shop_id и secret: из настроек админки (приоритет), иначе из .env."""
    shop_id = settings.YOOKASSA_SHOP_ID
    secret = settings.YOOKASSA_SECRET
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        shop_id = (cfg.get("yookassa_shop_id") or "").strip() or shop_id
        secret = (cfg.get("yookassa_secret") or "").strip() or secret
    except Exception as exc:
        logger.warning("[YooKassa] не удалось прочитать настройки: %s", exc)
    return shop_id, secret


async def create_payment(
    order_id: int,
    amount: int,           # рубли (целые)
    description: str,
    return_url: str,
    receipt: dict | None = None,   # чек 54-ФЗ (customer + items), если требуется кассой
) -> dict:
    """
    Создаёт платёж в YooKassa.
    Возвращает {"payment_id": ..., "confirmation_url": ...} или
    {"payment_id": None, "confirmation_url": None} если нет ключей.
    """
    shop_id, secret = await _credentials()
    if not shop_id or not secret:
        logger.warning("[YooKassa] нет ключей, платёж не создан для order_id=%s", order_id)
        return {"payment_id": None, "confirmation_url": None}

    idempotence_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"order-{order_id}"))

    payload = {
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
        "metadata": {"order_id": order_id},
    }
    if receipt:
        payload["receipt"] = receipt

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{YOOKASSA_API}/payments",
                auth=(shop_id, secret),
                headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
                content=json.dumps(payload),
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("[YooKassa] ошибка создания платежа order=%s: %s %s",
                     order_id, exc.response.status_code, exc.response.text[:300])
        return {"payment_id": None, "confirmation_url": None}
    except Exception as exc:
        logger.error("[YooKassa] исключение при создании платежа order=%s: %s", order_id, exc)
        return {"payment_id": None, "confirmation_url": None}

    payment_id = data["id"]
    confirmation_url = data.get("confirmation", {}).get("confirmation_url")
    logger.info("[YooKassa] payment created: %s for order %s", payment_id, order_id)
    return {"payment_id": payment_id, "confirmation_url": confirmation_url}


async def refund_payment(payment_id: str, amount: int) -> dict:
    shop_id, secret = await _credentials()
    if not shop_id or not secret:
        logger.warning("[YooKassa] нет ключей, возврат не создан для %s", payment_id)
        return {"refund_id": None}

    idempotence_key = str(uuid.uuid4())  # каждый возврат — отдельная операция

    payload = {
        "payment_id": payment_id,
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{YOOKASSA_API}/refunds",
                auth=(shop_id, secret),
                headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
                content=json.dumps(payload),
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("[YooKassa] ошибка возврата %s: %s", payment_id, exc)
        return {"refund_id": None}

    return {"refund_id": data.get("id")}


# Официальные подсети, с которых YooKassa шлёт уведомления (webhooks)
_WEBHOOK_NETS = [
    ipaddress.ip_network(n) for n in (
        "185.71.76.0/27", "185.71.77.0/27",
        "77.75.153.0/25", "77.75.156.11/32",
        "77.75.156.35/32", "77.75.154.128/25",
        "2a02:5180::/32",
    )
]


def verify_webhook_ip(client_ip: str) -> bool:
    """Проверяет, что вебхук пришёл с разрешённого IP YooKassa."""
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip in net for net in _WEBHOOK_NETS)
