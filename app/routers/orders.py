import logging
from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import (
    Order, OrderItem, OrderStatus, PaymentMethod, PaymentStatus,
    Product, DeliveryZone, User, Promocode,
)
from app.services.zones import find_zone
from app.services.dadata import suggest_address, geocode_reverse
from app.services.yookassa import create_payment
from app.services.promo import validate_promocode, spend_cashback, earn_cashback, get_cashback_balance
from app.services.notifications import notify_order_new
from app.deps import get_optional_user
from app.config import settings
from app.templates_env import templates

router = APIRouter(tags=["orders"])
pages_router = APIRouter(tags=["pages"])

MIN_ORDER_SUM = 500  # рублей


# ─── Схемы ────────────────────────────────────────────────────────────────────

class OrderItemIn(BaseModel):
    product_id: int
    quantity: int
    price: int


class ValidatePromoIn(BaseModel):
    code: str
    subtotal: int


class CreateOrderIn(BaseModel):
    name: str
    phone: str
    address: str
    delivery_mode: str = "delivery"
    delivery_date: date
    slot_start: str
    slot_end: str
    schedule_entry_id: Optional[int] = None
    payment_method: str = "online"
    cash_change_from: Optional[int] = None
    comment: str = ""
    promocode: Optional[str] = None
    cashback_spend: int = 0
    items: list[OrderItemIn]


# ─── Страница оформления ──────────────────────────────────────────────────────

@pages_router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request):
    return templates.TemplateResponse("checkout.html", {"request": request})


@pages_router.get("/order/{order_id}", response_class=HTMLResponse)
async def order_success_page(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
    items = items_res.scalars().all()

    return templates.TemplateResponse("order_success.html", {
        "request": request,
        "order": order,
        "items": items,
    })


# ─── API: проверка промокода ──────────────────────────────────────────────────

@router.post("/promocode/validate")
async def api_validate_promocode(
    payload: ValidatePromoIn,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    result = await validate_promocode(
        db=db,
        code=payload.code,
        user_id=current_user.id if current_user else None,
        subtotal=payload.subtotal,
    )
    if not result["valid"]:
        return {"valid": False, "message": result["message"]}

    promo: Promocode = result["promo"]
    return {
        "valid": True,
        "message": result["message"],
        "discount": result["discount"],
        "type": promo.type.value,
        "cashback_pct": promo.cashback_buyer_pct,
    }


# ─── API: доступный кешбэк пользователя ──────────────────────────────────────

@router.get("/cashback/balance")
async def cashback_balance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    if not current_user:
        return {"balance": 0}
    balance = await get_cashback_balance(db, current_user.id)
    return {"balance": balance}


# ─── API: создать заказ ───────────────────────────────────────────────────────

@router.post("/orders")
async def create_order(
    payload: CreateOrderIn,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    # товары из БД
    product_ids = [i.product_id for i in payload.items]
    result = await db.execute(select(Product).where(Product.id.in_(product_ids)))
    db_products = {p.id: p for p in result.scalars().all()}

    order_items_data = []
    subtotal = 0
    for item in payload.items:
        p = db_products.get(item.product_id)
        if not p:
            raise HTTPException(status_code=400, detail=f"Товар {item.product_id} не найден")
        if not p.is_visible or p.is_deleted:
            raise HTTPException(status_code=400, detail=f"Товар «{p.name}» отсутствует")
        line_total = p.price * item.quantity
        subtotal += line_total
        order_items_data.append({
            "product_id": p.id,
            "product_name": p.name,
            "product_price": p.price,
            "quantity": item.quantity,
            "line_total": line_total,
        })

    if subtotal < MIN_ORDER_SUM:
        raise HTTPException(
            status_code=400,
            detail=f"Минимальная сумма заказа {MIN_ORDER_SUM} ₽, у вас {subtotal} ₽",
        )

    # промокод
    promo_obj: Optional[Promocode] = None
    discount_amount = 0
    if payload.promocode:
        promo_result = await validate_promocode(
            db=db,
            code=payload.promocode,
            user_id=current_user.id if current_user else None,
            subtotal=subtotal,
        )
        if not promo_result["valid"]:
            raise HTTPException(status_code=400, detail=promo_result["message"])
        promo_obj = promo_result["promo"]
        discount_amount = promo_result["discount"]

    # кешбэк — списание
    cashback_spend = 0
    if payload.cashback_spend > 0 and current_user:
        balance = await get_cashback_balance(db, current_user.id)
        cashback_spend = min(payload.cashback_spend, balance, subtotal - discount_amount)
        if cashback_spend < 0:
            cashback_spend = 0

    # доставка (зоны реализованы, здесь берём 0 как default)
    delivery_price = 0
    total_amount = subtotal - discount_amount - cashback_spend + delivery_price
    if total_amount < 1:
        total_amount = 1

    try:
        payment_method = PaymentMethod(payload.payment_method)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный способ оплаты")

    slot_start = _parse_time(payload.slot_start)
    slot_end   = _parse_time(payload.slot_end)

    order = Order(
        user_id=current_user.id if current_user else None,
        phone=payload.phone,
        address=payload.address,
        delivery_date=payload.delivery_date,
        slot_start=slot_start,
        slot_end=slot_end,
        schedule_entry_id=payload.schedule_entry_id,
        payment_method=payment_method,
        cash_change_from=payload.cash_change_from,
        status=OrderStatus.new,
        payment_status=PaymentStatus.pending,
        promocode_id=promo_obj.id if promo_obj else None,
        discount_amount=discount_amount,
        cashback_spent=cashback_spend,
        delivery_price=delivery_price,
        total_amount=total_amount,
    )
    db.add(order)
    await db.flush()

    for d in order_items_data:
        db.add(OrderItem(order_id=order.id, **d))

    # списываем кешбэк до commit
    if cashback_spend > 0 and current_user:
        await spend_cashback(db, current_user.id, cashback_spend, order.id)

    # инкрементируем счётчик промокода
    if promo_obj:
        promo_obj.usage_count += 1

    await db.commit()

    # начисляем кешбэк (после оплаты по факту — но для cash/terminal сразу)
    if current_user and payment_method != PaymentMethod.online:
        earned = await earn_cashback(db, current_user.id, order, promo_obj)
        order.cashback_earned = earned
        await db.commit()

    # уведомление о новом заказе
    try:
        await notify_order_new(db, order)
    except Exception as exc:
        logger.warning("[notify] ошибка при отправке уведомления: %s", exc)

    # YooKassa
    if payment_method == PaymentMethod.online:
        return_url = f"{settings.SITE_URL}/pay/{order.id}"
        try:
            yk = await create_payment(
                order_id=order.id,
                amount=total_amount,
                description=f"Заказ #{order.id} · Вкусно Томск",
                return_url=return_url,
            )
        except Exception:
            yk = {"payment_id": None, "confirmation_url": None}

        if yk["payment_id"]:
            order.yookassa_payment_id = yk["payment_id"]
            await db.commit()

        payment_url = yk["confirmation_url"] or f"{settings.SITE_URL}/pay/{order.id}"
        return {"order_id": order.id, "payment_url": payment_url}

    return {"order_id": order.id}


# ─── API: определение зоны доставки ──────────────────────────────────────────

async def _dadata_token(db: AsyncSession) -> str:
    """Ключ Dadata: из site_settings (приоритет), иначе из .env."""
    from app.services.settings import get_site_settings
    cfg = await get_site_settings(db)
    return (cfg.get("dadata_token") or "").strip() or settings.DADATA_TOKEN


@router.get("/address/suggest")
async def address_suggest(q: str, db: AsyncSession = Depends(get_db)):
    """Подсказки адреса (Dadata). Пусто, если токен не настроен."""
    try:
        token = await _dadata_token(db)
        return {"suggestions": await suggest_address(q, token=token)}
    except Exception as exc:
        logger.warning("[dadata] suggest error: %s", exc)
        return {"suggestions": []}


@router.get("/address/geocode")
async def address_geocode(lat: float, lon: float, db: AsyncSession = Depends(get_db)):
    """Обратное геокодирование клика по карте → адрес."""
    try:
        token = await _dadata_token(db)
        return {"address": await geocode_reverse(lat, lon, token=token)}
    except Exception as exc:
        logger.warning("[dadata] geocode error: %s", exc)
        return {"address": None}


@router.get("/address/detect-zone")
async def detect_zone(lat: float, lon: float, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DeliveryZone).where(DeliveryZone.is_active == True))
    zones = result.scalars().all()
    zone = find_zone(lat, lon, zones)
    if not zone:
        return {"zone": None, "delivery_price": None, "message": "Адрес вне зоны доставки"}
    return {
        "zone": {"id": zone.id, "name": zone.name, "color": zone.color},
        "delivery_price": zone.delivery_price,
        "free_delivery_from": zone.free_delivery_from,
        "min_order_sum": zone.min_order_sum,
    }


# ─── Утилита ──────────────────────────────────────────────────────────────────

def _parse_time(s: str) -> time:
    try:
        parts = s.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(10, 0)
