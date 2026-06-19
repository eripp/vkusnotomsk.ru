"""Промокоды и кешбэк."""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    CashbackAccount, CashbackTransaction, CashbackTxType,
    Order, Promocode, PromoType, SiteSetting,
)


# ─── Промокод ─────────────────────────────────────────────────────────────────

async def validate_promocode(
    db: AsyncSession,
    code: str,
    user_id: int | None,
    subtotal: int,
) -> dict:
    """
    Возвращает:
      {"valid": True, "promo": <Promocode>, "discount": int, "message": str}
    или
      {"valid": False, "message": str}
    """
    now = datetime.utcnow()
    result = await db.execute(
        select(Promocode).where(
            Promocode.code == code.upper().strip(),
            Promocode.is_active == True,
            Promocode.valid_from <= now,
            Promocode.valid_until >= now,
        )
    )
    promo = result.scalar_one_or_none()

    if not promo:
        return {"valid": False, "message": "Промокод не найден или истёк"}

    if promo.usage_limit is not None and promo.usage_count >= promo.usage_limit:
        return {"valid": False, "message": "Промокод исчерпан"}

    if promo.min_order_amount and subtotal < promo.min_order_amount:
        return {
            "valid": False,
            "message": f"Минимальная сумма для промокода: {promo.min_order_amount} ₽",
        }

    discount = 0
    if promo.type == PromoType.discount and promo.discount_percent:
        discount = subtotal * promo.discount_percent // 100

    return {
        "valid": True,
        "promo": promo,
        "discount": discount,
        "message": _promo_label(promo, discount),
    }


def _promo_label(promo: Promocode, discount: int) -> str:
    if promo.type == PromoType.discount:
        return f"Скидка {promo.discount_percent}% — −{discount} ₽"
    if promo.type == PromoType.cashback:
        pct = promo.cashback_buyer_pct or 0
        return f"Кешбэк {pct}% на этот заказ"
    if promo.type == PromoType.referral:
        pct = promo.cashback_buyer_pct or 0
        return f"Реферальный код — кешбэк {pct}%"
    return "Промокод применён"


# ─── Кешбэк ───────────────────────────────────────────────────────────────────

async def get_cashback_balance(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user_id)
    )
    acc = result.scalar_one_or_none()
    return acc.balance if acc else 0


async def earn_cashback(
    db: AsyncSession,
    user_id: int,
    order: Order,
    promo: Promocode | None,
) -> int:
    """Начисляем кешбэк за заказ. Возвращает начисленную сумму."""
    # процент кешбэка: из промокода или из настроек сайта
    pct = 0
    if promo and promo.type in (PromoType.cashback, PromoType.referral):
        pct = promo.cashback_buyer_pct or 0
    else:
        # берём из site_settings
        cfg_res = await db.execute(
            select(SiteSetting).where(SiteSetting.key == "cashback_max_pct")
        )
        cfg = cfg_res.scalar_one_or_none()
        if cfg:
            try:
                pct = int(cfg.value)
            except (ValueError, TypeError):
                pct = 0

    if pct <= 0:
        return 0

    # кешбэк начисляется с суммы после скидки, не с доставки
    base = order.total_amount - order.delivery_price - order.discount_amount
    earned = max(0, base * pct // 100)
    if earned == 0:
        return 0

    await _apply_cashback_delta(db, user_id, earned, CashbackTxType.earn, order_id=order.id,
                                comment=f"Кешбэк {pct}% за заказ #{order.id}")
    return earned


async def spend_cashback(
    db: AsyncSession,
    user_id: int,
    amount: int,
    order_id: int,
) -> None:
    """Списываем кешбэк при оплате заказа."""
    if amount <= 0:
        return
    balance = await get_cashback_balance(db, user_id)
    if amount > balance:
        raise ValueError(f"Недостаточно бонусов: баланс {balance}, запрошено {amount}")
    await _apply_cashback_delta(db, user_id, -amount, CashbackTxType.spend, order_id=order_id,
                                comment=f"Списание за заказ #{order_id}")


async def _apply_cashback_delta(
    db: AsyncSession,
    user_id: int,
    delta: int,
    tx_type: CashbackTxType,
    order_id: int | None = None,
    comment: str | None = None,
) -> None:
    result = await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user_id)
    )
    acc = result.scalar_one_or_none()
    if not acc:
        acc = CashbackAccount(user_id=user_id, balance=0)
        db.add(acc)
        await db.flush()

    acc.balance = max(0, acc.balance + delta)

    tx = CashbackTransaction(
        user_id=user_id,
        type=tx_type,
        amount=abs(delta),
        order_id=order_id,
        comment=comment,
    )
    db.add(tx)
