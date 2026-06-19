import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Order, OrderItem, Promocode, PaymentStatus, OrderStatus
from app.services.yookassa import verify_webhook_ip
from app.services.notifications import notify_order_status
from app.services.promo import earn_cashback
from app.templates_env import templates

router = APIRouter(tags=["payment"])
pages_router = APIRouter(tags=["pages"])
logger = logging.getLogger(__name__)


# ─── Страница ожидания / результата оплаты ───────────────────────────────────

@pages_router.get("/pay/{order_id}", response_class=HTMLResponse)
async def pay_wait_page(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """Промежуточная страница: показываем спиннер, JS поллит статус."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return templates.TemplateResponse("pay_wait.html", {
        "request": request,
        "order": order,
    })


@router.get("/status/{order_id}")
async def payment_status(order_id: int, db: AsyncSession = Depends(get_db)):
    """JS поллит этот эндпоинт каждые 3 сек."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404)
    return {
        "order_id": order.id,
        "payment_status": order.payment_status.value,
        "order_status": order.status.value,
    }


# ─── Webhook от YooKassa ──────────────────────────────────────────────────────

@router.post("/callback")
async def payment_callback(request: Request, db: AsyncSession = Depends(get_db)):
    # проверяем IP (на проде включить)
    # client_ip = request.client.host
    # if not verify_webhook_ip(client_ip):
    #     raise HTTPException(status_code=403)

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
    order_id   = metadata.get("order_id")
    if not order_id:
        logger.warning("[YooKassa] payment.succeeded без order_id: %s", payment_id)
        return

    result = await db.execute(select(Order).where(Order.id == int(order_id)))
    order = result.scalar_one_or_none()
    if not order:
        return

    order.payment_status = PaymentStatus.paid
    if order.status == OrderStatus.new:
        order.status = OrderStatus.accepted
    order.yookassa_payment_id = payment_id
    await db.commit()
    logger.info("[YooKassa] заказ #%s оплачен", order_id)

    # начисляем кешбэк за онлайн-оплату
    if order.user_id:
        promo = None
        if order.promocode_id:
            pr = await db.execute(select(Promocode).where(Promocode.id == order.promocode_id))
            promo = pr.scalar_one_or_none()
        try:
            earned = await earn_cashback(db, order.user_id, order, promo)
            if earned:
                order.cashback_earned = earned
                await db.commit()
        except Exception as exc:
            logger.warning("[cashback] ошибка начисления: %s", exc)

    # уведомление клиенту
    try:
        await notify_order_status(db, order)
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
