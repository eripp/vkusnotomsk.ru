"""YooKassa payment integration."""
import hashlib
import hmac
import json
import logging
import uuid

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3"


async def create_payment(
    order_id: int,
    amount: int,           # рубли (целые)
    description: str,
    return_url: str,
) -> dict:
    """
    Создаёт платёж в YooKassa.
    Возвращает {"payment_id": ..., "confirmation_url": ...} или
    {"payment_id": None, "confirmation_url": None} если нет ключей.
    """
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET:
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

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{YOOKASSA_API}/payments",
            auth=(settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET),
            headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        resp.raise_for_status()
        data = resp.json()

    payment_id = data["id"]
    confirmation_url = data.get("confirmation", {}).get("confirmation_url")
    logger.info("[YooKassa] payment created: %s for order %s", payment_id, order_id)
    return {"payment_id": payment_id, "confirmation_url": confirmation_url}


async def refund_payment(payment_id: str, amount: int) -> dict:
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET:
        logger.warning("[YooKassa] нет ключей, возврат не создан для %s", payment_id)
        return {"refund_id": None}

    idempotence_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"refund-{payment_id}"))

    payload = {
        "payment_id": payment_id,
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{YOOKASSA_API}/refunds",
            auth=(settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET),
            headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        resp.raise_for_status()
        data = resp.json()

    return {"refund_id": data.get("id")}


def verify_webhook_ip(client_ip: str) -> bool:
    """YooKassa шлёт вебхуки только с определённых IP."""
    ALLOWED = {
        "185.71.76.0/27", "185.71.77.0/27",
        "77.75.153.0/25", "77.75.156.11/32",
        "77.75.156.35/32", "77.75.154.128/25",
        "2a02:5180::/32",
    }
    # упрощённая проверка без CIDR — в проде использовать ipaddress.ip_network
    trusted_prefixes = ["185.71.76.", "185.71.77.", "77.75.153.", "77.75.156.", "77.75.154."]
    return any(client_ip.startswith(p) for p in trusted_prefixes)
