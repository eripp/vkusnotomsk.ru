import logging
from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import (
    Order, OrderItem, OrderStatus, PaymentMethod, PaymentStatus,
    Product, DeliveryZone, User, Promocode, PendingOrder, CartItem,
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
    lat: Optional[float] = None
    lon: Optional[float] = None
    delivery_mode: str = "delivery"
    delivery_date: date
    slot_start: str
    slot_end: str
    schedule_entry_id: Optional[int] = None
    payment_method: str = "online"
    cash_change_from: Optional[int] = None
    persons_count: int = 0
    comment: str = ""
    promocode: Optional[str] = None
    cashback_spend: int = 0
    items: list[OrderItemIn]


# ─── Материализация заказа из подготовленных данных ──────────────────────────

async def _materialize_order(db: AsyncSession, data: dict, paid: bool) -> Order:
    """Создаёт Order + позиции из словаря order_data. Списывает кешбэк,
    инкрементит промокод. Если paid=True — статус оплачен + начисление кешбэка.
    Используется и при cash/terminal (сразу), и в webhook после онлайн-оплаты."""
    order = Order(
        user_id=data.get("user_id"),
        customer_name=data.get("customer_name"),
        phone=data["phone"],
        address=data["address"],
        address_lat=data.get("lat"),
        address_lon=data.get("lon"),
        zone_id=data.get("zone_id"),
        delivery_date=date.fromisoformat(data["delivery_date"]),
        slot_start=_parse_time(data["slot_start"]),
        slot_end=_parse_time(data["slot_end"]),
        schedule_entry_id=data.get("schedule_entry_id"),
        payment_method=PaymentMethod(data["payment_method"]),
        cash_change_from=data.get("cash_change_from"),
        persons_count=data.get("persons_count", 0),
        status=OrderStatus.new,
        payment_status=PaymentStatus.paid if paid else PaymentStatus.pending,
        promocode_id=data.get("promocode_id"),
        discount_amount=data.get("discount_amount", 0),
        cashback_spent=data.get("cashback_spend", 0),
        delivery_price=data.get("delivery_price", 0),
        total_amount=data["total_amount"],
    )
    db.add(order)
    await db.flush()

    for it in data["items"]:
        db.add(OrderItem(
            order_id=order.id, product_id=it["product_id"],
            product_name=it["product_name"], product_price=it["product_price"],
            quantity=it["quantity"], line_total=it["line_total"],
        ))

    # списание кешбэка
    if data.get("cashback_spend", 0) > 0 and order.user_id:
        await spend_cashback(db, order.user_id, data["cashback_spend"], order.id)

    # инкремент промокода
    if data.get("promocode_id"):
        pr = (await db.execute(select(Promocode).where(Promocode.id == data["promocode_id"]))).scalar_one_or_none()
        if pr:
            pr.usage_count += 1

    # очищаем серверную корзину пользователя — заказ оформлен
    if order.user_id:
        await db.execute(delete(CartItem).where(CartItem.user_id == order.user_id))

    await db.commit()

    # начисление кешбэка — только для оплаченного заказа
    if paid and order.user_id:
        pr = None
        if data.get("promocode_id"):
            pr = (await db.execute(select(Promocode).where(Promocode.id == data["promocode_id"]))).scalar_one_or_none()
        try:
            earned = await earn_cashback(db, order.user_id, order, pr)
            if earned:
                order.cashback_earned = earned
                await db.commit()
        except Exception as exc:
            logger.warning("[cashback] начисление: %s", exc)

    return order


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

def _normalize_order_phone(phone: str) -> str:
    """Нормализует телефон заказа к +7XXXXXXXXXX. 400, если не 11 цифр
    (защита от пустого/мусорного значения вроде '+' в обход маски на фронте)."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) != 11 or not digits.startswith("7"):
        raise HTTPException(status_code=400, detail="Укажите корректный номер телефона")
    return "+" + digits


def _base_url(request: Request) -> str:
    """Базовый URL сайта по входящему запросу (с учётом проксирования за nginx).
    Используется для return_url YooKassa — чтобы вернуть на тот же домен,
    с которого пришёл покупатель (v2.vkusnotomsk.ru, основной и т.д.)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return settings.SITE_URL.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


@router.post("/orders")
async def create_order(
    request: Request,
    payload: CreateOrderIn,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    # Телефон обязателен и должен быть валидным (11 цифр). Нормализуем к +7XXXXXXXXXX
    # и подставляем обратно — иначе в заказ мог попасть мусор вроде "+" (обход маски).
    payload.phone = _normalize_order_phone(payload.phone)

    # если залогинен и имя в профиле пустое — сохраняем введённое в заказе
    if current_user and not (current_user.name or "").strip() and payload.name.strip():
        current_user.name = payload.name.strip()
        await db.commit()

    # товары из БД
    product_ids = [i.product_id for i in payload.items]
    result = await db.execute(select(Product).where(Product.id.in_(product_ids)))
    db_products = {p.id: p for p in result.scalars().all()}

    order_items_data: list[dict] = []
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

    # минимальная сумма заказа проверяется ниже — с учётом зоны (для доставки)

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

    # доставка: определяем зону по координатам и считаем цену с учётом порога
    # бесплатной доставки (порог сравниваем с суммой товаров).
    delivery_price = 0
    zone_id = None
    if payload.delivery_mode == "delivery":
        if payload.lat is None or payload.lon is None:
            raise HTTPException(status_code=400, detail="Укажите адрес доставки на карте")
        zres = await db.execute(select(DeliveryZone).where(DeliveryZone.is_active == True))
        zone = find_zone(payload.lat, payload.lon, zres.scalars().all())
        if not zone:
            raise HTTPException(status_code=400, detail="Адрес вне зоны доставки")
        zone_id = zone.id
        # минимальная сумма заказа — только из зоны (если задана)
        if zone.min_order_sum and subtotal < zone.min_order_sum:
            raise HTTPException(status_code=400, detail=f"Минимальная сумма заказа {zone.min_order_sum} ₽, у вас {subtotal} ₽")
        free_from = zone.free_delivery_from
        if free_from is not None and subtotal >= free_from:
            delivery_price = 0
        else:
            delivery_price = zone.delivery_price or 0

    total_amount = subtotal - discount_amount - cashback_spend + delivery_price
    if total_amount < 1:
        total_amount = 1

    try:
        payment_method = PaymentMethod(payload.payment_method)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный способ оплаты")

    # данные будущего заказа в едином виде (для немедленного создания или для
    # материализации после оплаты)
    order_data = {
        "user_id": current_user.id if current_user else None,
        "customer_name": (payload.name or "").strip() or None,
        "phone": payload.phone,
        "address": payload.address,
        "lat": payload.lat,
        "lon": payload.lon,
        "zone_id": zone_id,
        "delivery_date": payload.delivery_date.isoformat(),
        "slot_start": payload.slot_start,
        "slot_end": payload.slot_end,
        "schedule_entry_id": payload.schedule_entry_id,
        "payment_method": payment_method.value,
        "cash_change_from": payload.cash_change_from,
        "persons_count": max(0, min(100, payload.persons_count or 0)),   # приборы, 0..100
        "promocode_id": promo_obj.id if promo_obj else None,
        "discount_amount": discount_amount,
        "cashback_spend": cashback_spend,
        "delivery_price": delivery_price,
        "total_amount": total_amount,
        "items": order_items_data,
    }

    # ── Онлайн-оплата: заказ НЕ создаём, пока не пришла оплата ────────────────
    # Сохраняем черновик и создаём платёж; Order появится в webhook (paid).
    if payment_method == PaymentMethod.online:
        pending = PendingOrder(data=order_data)
        db.add(pending)
        await db.flush()

        email = current_user.email if current_user and current_user.email else None
        receipt = _build_receipt(
            items=order_items_data,
            delivery_price=delivery_price,
            reduction=discount_amount + cashback_spend,
            phone=payload.phone,
            email=email,
            total_amount=total_amount,
        )
        try:
            yk = await create_payment(
                order_id=pending.id,          # для metadata/idempotency используем pending.id
                amount=total_amount,
                description=f"Заказ · Вкусно Томск",
                return_url=f"{_base_url(request)}/pay/p{pending.id}",
                receipt=receipt,
            )
        except Exception:
            yk = {"payment_id": None, "confirmation_url": None}

        if not yk["confirmation_url"]:
            # платёж не создан — откатываем черновик, заказа нет
            await db.rollback()
            raise HTTPException(status_code=502, detail="Не удалось создать платёж. Попробуйте позже.")

        pending.yookassa_payment_id = yk["payment_id"]
        await db.commit()
        return {"pending_id": pending.id, "payment_url": yk["confirmation_url"]}

    # ── Наличные / терминал: создаём заказ сразу ─────────────────────────────
    order = await _materialize_order(db, order_data, paid=False)
    try:
        await notify_order_new(db, order)
    except Exception as exc:
        logger.warning("[notify] ошибка при отправке уведомления: %s", exc)
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


# ─── Чек 54-ФЗ для YooKassa ──────────────────────────────────────────────────

YOOKASSA_VAT_CODE = 7   # НДС 5% (УСН)


def _build_receipt(items: list[dict], delivery_price: int, reduction: int,
                   phone: str, email: str | None, total_amount: int) -> dict:
    """Чек: позиции товаров + доставка. Сумма позиций строго = total_amount.
    `reduction` (скидка промокода + списанный кешбэк) распределяется по товарам
    пропорционально; остаток от округления добивается на последней позиции."""
    goods_sum = sum(i["line_total"] for i in items)
    # целевая сумма по товарам (без доставки), которую должен дать чек
    target_goods = max(0, total_amount - delivery_price)

    lines = []
    allocated = 0
    n = len(items)
    for idx, it in enumerate(items):
        if idx < n - 1 and goods_sum > 0:
            val = round(it["line_total"] / goods_sum * target_goods)
        else:
            val = target_goods - allocated   # последняя позиция добивает до точной суммы
        allocated += val
        # YooKassa: amount.value — цена за ЕДИНИЦУ; сумма строки = amount × quantity.
        # Со скидками поштучная цена может не делиться нацело, поэтому делаем
        # quantity=1, а итог строки кладём в amount (кол-во отражаем в названии).
        qty = it["quantity"]
        desc = it["product_name"]
        if qty > 1:
            desc = f"{desc} ({qty} шт.)"
        lines.append({
            "description": desc[:128],
            "quantity": "1",
            "amount": {"value": f"{val}.00", "currency": "RUB"},
            "vat_code": YOOKASSA_VAT_CODE,
            "payment_subject": "commodity",
            "payment_mode": "full_payment",
            "measure": "piece",
        })

    if delivery_price > 0:
        lines.append({
            "description": "Доставка",
            "quantity": "1",
            "amount": {"value": f"{delivery_price}.00", "currency": "RUB"},
            "vat_code": YOOKASSA_VAT_CODE,
            "payment_subject": "service",
            "payment_mode": "full_payment",
            "measure": "piece",
        })

    customer = {}
    if email:
        customer["email"] = email
    if phone:
        customer["phone"] = "".join(c for c in phone if c.isdigit())
    return {"customer": customer, "items": lines}


# ─── Утилита ──────────────────────────────────────────────────────────────────

def _parse_time(s: str) -> time:
    try:
        parts = s.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(10, 0)
