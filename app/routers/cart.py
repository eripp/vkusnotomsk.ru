from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_optional_user
from app.models import Product, ProductImage, CartItem, User

router = APIRouter(tags=["cart"])


def _media_url(name: str | None) -> str | None:
    if not name:
        return None
    if name.startswith(("http://", "https://", "/")):
        return name
    return f"/media/{name}"


async def _server_cart(db: AsyncSession, user_id: int) -> list[dict]:
    """Серверная корзина пользователя с актуальными данными из Product.
    Недоступные товары (скрытые/удалённые) не возвращаем."""
    rows = (await db.execute(
        select(CartItem.qty, Product)
        .join(Product, Product.id == CartItem.product_id)
        .where(
            CartItem.user_id == user_id,
            Product.is_visible == True,
            Product.is_deleted == False,
        )
    )).all()
    if not rows:
        return []
    # обложка — первое фото товара по sort_order
    pids = [p.id for _, p in rows]
    img_rows = (await db.execute(
        select(ProductImage.product_id, ProductImage.url)
        .where(ProductImage.product_id.in_(pids))
        .order_by(ProductImage.product_id, ProductImage.sort_order)
    )).all()
    cover: dict[int, str] = {}
    for pid, url in img_rows:
        cover.setdefault(pid, url)
    return [
        {
            "id": p.id, "name": p.name, "price": p.price,
            "weight": p.weight or "", "image": _media_url(cover.get(p.id)) or "",
            "qty": qty,
        }
        for qty, p in rows
    ]


@router.get("/cart")
async def get_cart():
    return {"items": []}


@router.get("/cart/items")
async def get_cart_items(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Серверная корзина залогиненного пользователя (для подтягивания на вход)."""
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return {"items": await _server_cart(db, user.id)}


class CartSyncItem(BaseModel):
    id: int
    qty: int


class CartSyncIn(BaseModel):
    items: list[CartSyncItem]


@router.put("/cart/items")
async def put_cart_items(
    payload: CartSyncIn,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Полностью заменяет серверную корзину пользователя (дебаунс-синк с фронта).
    Возвращает нормализованную корзину с актуальными ценами/наличием."""
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    # схлопываем дубли и отбрасываем некорректные qty
    wanted: dict[int, int] = {}
    for it in payload.items:
        if it.qty > 0:
            wanted[it.id] = wanted.get(it.id, 0) + it.qty

    # оставляем только существующие доступные товары
    if wanted:
        valid_ids = set((await db.execute(
            select(Product.id).where(
                Product.id.in_(wanted.keys()),
                Product.is_visible == True,
                Product.is_deleted == False,
            )
        )).scalars().all())
    else:
        valid_ids = set()

    await db.execute(delete(CartItem).where(CartItem.user_id == user.id))
    for pid, qty in wanted.items():
        if pid in valid_ids:
            db.add(CartItem(user_id=user.id, product_id=pid, qty=qty))
    await db.commit()

    return {"items": await _server_cart(db, user.id)}


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
