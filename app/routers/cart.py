from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Product

router = APIRouter(tags=["cart"])


@router.get("/cart")
async def get_cart():
    return {"items": []}


class ValidateCartIn(BaseModel):
    ids: list[int]


@router.post("/cart/validate")
async def validate_cart(payload: ValidateCartIn, db: AsyncSession = Depends(get_db)):
    """Проверяет доступность товаров корзины. Возвращает id недоступных
    (скрытых/удалённых/несуществующих) товаров — фронт убирает их из корзины."""
    if not payload.ids:
        return {"unavailable": []}

    rows = (await db.execute(
        select(Product.id).where(
            Product.id.in_(payload.ids),
            Product.is_visible == True,
            Product.is_deleted == False,
        )
    )).scalars().all()
    available = set(rows)
    unavailable = [pid for pid in payload.ids if pid not in available]
    return {"unavailable": unavailable}
