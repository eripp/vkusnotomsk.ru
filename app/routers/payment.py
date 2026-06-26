import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Order, OrderItem, Promocode, PaymentStatus, OrderStatus, PendingOrder
from app.services.yookassa import verify_webhook_ip
from app.services.notifications import notify_order_status, notify_order_new
from app.services.promo import earn_cashback
from app.templates_env import templates

router = APIRouter(tags=["payment"])
pages_router = APIRouter(tags=["pages"])
logger = logging.getLogger(__name__)


# ─── Страница ожидания / результата оплаты ───────────────────────────────────

@pages_router.get("/pay/{ref}", response_class=HTMLResponse)
async def pay_wait_page(request: Request, ref: str, db: AsyncSession = Depends(get_db)):
    """Страница ожидания оплаты. ref = 'p<pending_id>' (онлайн-черновик) или
    '<order_id>' (готовый заказ). JS поллит статус."""
    return templates.TemplateResponse("pay_wait.html", {"request": request, "ref": ref})


@router.get("/status/{ref}")
async def payment_status(ref: str, db: AsyncSession = Depends(get_db)):
    """JS поллит каждые 3 сек. Возвращает paid + order_id когда заказ создан."""
    if ref.startswith("p"):
        # черновик: оплачен, когда материализован в заказ (order_id заполнен)
        pid = int(ref[1:]) if ref[1:].isdigit() else None
        pending = (await db.execute(select(PendingOrder).where(PendingOrder.id == pid))).scalar_one_or_none() if pid else None
        if not pending:
            raise HTTPException(status_code=404)
        if pending.order_id:
            return {"paid": True, "order_id": pending.order_id}
        return {"paid": False, "order_id": None}
    # готовый заказ (cash/terminal)
    order = (await db.execute(select(Order).where(Order.id == int(ref)))).scalar_one_or_none() if ref.isdigit() else None
    if not order:
        raise HTTPException(status_code=404)
    return {"paid": order.payment_status.value == "paid", "order_id": order.id}


# ─── Webhook от YooKassa ──────────────────────────────────────────────────────

@router.post("/callback")
async def payment_callback(request: Request, db: AsyncSession = Depends(get_db)):
    # IP отправителя: за nginx берём первый из X-Forwarded-For, иначе client.host
    xff = request.headers.get("x-forwarded-for", "")
    client_ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "")
    if not verify_webhook_ip(client_ip):
        logger.warning("[YooKassa webhook] отклонён IP=%s", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event")
    obj   = body.get("object", {})

    logger.info("[YooKassa webhook] event=%s payment_id=%s", event, obj.get("id"))

    if event == "payment.succeeded":
        await _handle_payment_succeeded(db, obj)
    elif event == "payment.canceled":
        await _handle_payment_canceled(db, obj)
    elif event == "refund.succeeded":
        await _handle_refund_succeeded(db, obj)

    return {"status": "ok"}


async def _handle_payment_succeeded(db: AsyncSession, obj: dict):
    payment_id = obj.get("id")
    metadata   = obj.get("metadata", {})
    pending_id = metadata.get("order_id")   # это id черновика (PendingOrder)
    if not pending_id:
        logger.warning("[YooKassa] payment.succeeded без id: %s", payment_id)
        return

    pres = await db.execute(select(PendingOrder).where(PendingOrder.id == int(pending_id)))
    pending = pres.scalar_one_or_none()
    if not pending:
        logger.warning("[YooKassa] черновик %s не найден (payment %s)", pending_id, payment_id)
        return

    # идемпотентность: если заказ из этого черновика уже создан — выходим
    if pending.order_id:
        logger.info("[YooKassa] черновик %s уже материализован в заказ #%s", pending_id, pending.order_id)
        return

    # материализуем заказ (оплачен)
    from app.routers.orders import _materialize_order
    order = await _materialize_order(db, pending.data, paid=True)
    order.yookassa_payment_id = payment_id
    pending.order_id = order.id
    await db.commit()
    logger.info("[YooKassa] оплачен черновик %s → заказ #%s", pending_id, order.id)

    # уведомление клиенту о новом (оплаченном) заказе
    try:
        await notify_order_new(db, order)
    except Exception as exc:
        logger.warning("[notify] ошибка: %s", exc)


async def _handle_payment_canceled(db: AsyncSession, obj: dict):
    payment_id = obj.get("id")
    metadata   = obj.get("metadata", {})
    order_id   = metadata.get("order_id")
    if not order_id:
        return

    result = await db.execute(select(Order).where(Order.id == int(order_id)))
    order = result.scalar_one_or_none()
    if not order:
        return

    order.yookassa_payment_id = payment_id
    # не меняем статус заказа — пользователь может попробовать снова
    await db.commit()
    logger.info("[YooKassa] платёж отменён для заказа #%s", order_id)
    try:
        await notify_order_status(db, order)
    except Exception as exc:
        logger.warning("[notify] ошибка: %s", exc)


async def _handle_refund_succeeded(db: AsyncSession, obj: dict):
    payment_id = obj.get("payment_id")
    if not payment_id:
        return

    result = await db.execute(
        select(Order).where(Order.yookassa_payment_id == payment_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return

    order.payment_status = PaymentStatus.refunded
    await db.commit()
    logger.info("[YooKassa] возврат по платежу %s (заказ #%s)", payment_id, order.id)
