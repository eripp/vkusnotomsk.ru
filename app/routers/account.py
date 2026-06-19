from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    CashbackAccount, CashbackTransaction, NotificationSettings,
    Order, OrderItem, User,
)
from app.templates_env import templates

router = APIRouter(tags=["account"])


# ─── Схемы ────────────────────────────────────────────────────────────────────

class UpdateProfileIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class UpdateNotificationsIn(BaseModel):
    email_orders: Optional[bool] = None
    email_promo: Optional[bool] = None
    max_orders: Optional[bool] = None
    max_promo: Optional[bool] = None
    tg_orders: Optional[bool] = None
    tg_promo: Optional[bool] = None


# ─── SSR страница ─────────────────────────────────────────────────────────────

@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    return templates.TemplateResponse("account.html", {"request": request})


# ─── API: профиль ─────────────────────────────────────────────────────────────

@router.patch("/api/account/profile")
async def update_profile(
    payload: UpdateProfileIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.name is not None:
        user.name = payload.name.strip() or None
    if payload.email is not None:
        user.email = payload.email.strip() or None
    await db.commit()
    return {"id": user.id, "phone": user.phone, "name": user.name, "email": user.email}


# ─── API: история заказов ─────────────────────────────────────────────────────

@router.get("/api/account/orders")
async def account_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(desc(Order.created_at))
        .limit(50)
    )
    orders = result.scalars().all()

    order_ids = [o.id for o in orders]
    items_map: dict[int, list] = {o.id: [] for o in orders}
    if order_ids:
        items_res = await db.execute(
            select(OrderItem).where(OrderItem.order_id.in_(order_ids))
        )
        for item in items_res.scalars().all():
            items_map[item.order_id].append({
                "product_name": item.product_name,
                "quantity": item.quantity,
                "line_total": item.line_total,
            })

    return [
        {
            "id": o.id,
            "status": o.status.value,
            "payment_method": o.payment_method.value,
            "payment_status": o.payment_status.value,
            "total_amount": o.total_amount,
            "delivery_date": o.delivery_date.isoformat(),
            "slot_start": o.slot_start.strftime("%H:%M"),
            "slot_end": o.slot_end.strftime("%H:%M"),
            "address": o.address,
            "created_at": o.created_at.isoformat(),
            "items": items_map[o.id],
        }
        for o in orders
    ]


# ─── API: кешбэк ──────────────────────────────────────────────────────────────

@router.get("/api/account/cashback")
async def account_cashback(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acc_res = await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user.id)
    )
    acc = acc_res.scalar_one_or_none()
    balance = acc.balance if acc else 0

    txs_res = await db.execute(
        select(CashbackTransaction)
        .where(CashbackTransaction.user_id == user.id)
        .order_by(desc(CashbackTransaction.created_at))
        .limit(20)
    )
    txs = txs_res.scalars().all()

    return {
        "balance": balance,
        "transactions": [
            {
                "type": t.type.value,
                "amount": t.amount,
                "comment": t.comment,
                "order_id": t.order_id,
                "created_at": t.created_at.isoformat(),
            }
            for t in txs
        ],
    }


# ─── API: настройки уведомлений ───────────────────────────────────────────────

@router.get("/api/account/notifications")
async def get_notifications(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(NotificationSettings).where(NotificationSettings.user_id == user.id)
    )
    ns = res.scalar_one_or_none()
    if not ns:
        return {
            "email_orders": True, "email_promo": False,
            "max_orders": True, "max_promo": False,
            "tg_orders": True, "tg_promo": False,
        }
    return {
        "email_orders": ns.email_orders, "email_promo": ns.email_promo,
        "max_orders": ns.max_orders, "max_promo": ns.max_promo,
        "tg_orders": ns.tg_orders, "tg_promo": ns.tg_promo,
    }


@router.patch("/api/account/notifications")
async def update_notifications(
    payload: UpdateNotificationsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(NotificationSettings).where(NotificationSettings.user_id == user.id)
    )
    ns = res.scalar_one_or_none()
    if not ns:
        ns = NotificationSettings(user_id=user.id)
        db.add(ns)

    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(ns, field, val)

    await db.commit()
    return {"status": "ok"}


# ─── API: привязка Telegram ───────────────────────────────────────────────────

@router.post("/api/account/tg-link")
async def tg_link(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.config import settings
    from app.routers.tgbot import make_link_token
    token = make_link_token(user.id)
    bot_username = settings.TG_BOT_USERNAME
    if not bot_username:
        return {"url": None, "message": "Telegram Bot не настроен"}
    url = f"https://t.me/{bot_username}?start={token}"
    return {"url": url}


@router.delete("/api/account/tg-link")
async def tg_unlink(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.tg_chat_id = None
    await db.commit()
    return {"status": "ok"}


# ─── API: выход ───────────────────────────────────────────────────────────────

@router.post("/api/account/logout")
async def account_logout(response: Response):
    response.delete_cookie("vkusno_token")
    return {"status": "ok"}
