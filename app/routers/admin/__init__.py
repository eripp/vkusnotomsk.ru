import csv
import io
import logging
import os
import uuid
from datetime import date, datetime, time, timedelta
from typing import Optional

from PIL import Image as PILImage

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select, update, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi import Cookie

from app.config import settings
from app.database import get_db
from app.models import (
    AdminUser, AuditEvent, Category, CashbackAccount, CashbackTransaction, CashbackTxType,
    DeliveryZone, Order, OrderItem,
    OrderStatus, PaymentMethod, PaymentStatus, Product, ProductGroup, ProductImage,
    Promocode, PromoType, ScheduleEntry, ScheduleEntryType, SiteSetting, Story, StorySlide, User,
)
from app.services.notifications import notify_order_status
from app.services import admin_auth
from app.services import audit

logger = logging.getLogger(__name__)


# ─── Гард доступа ─────────────────────────────────────────────────────────────
# Любой /admin/* без валидной сессии → 404 (админка полностью скрыта от чужих).
async def require_admin(
    request: Request,
    vkusno_admin: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    admin_id = admin_auth.decode_admin_session(vkusno_admin)
    if admin_id is not None:
        admin = (
            await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
        ).scalar_one_or_none()
        if admin and admin.is_active:
            request.state.admin = admin   # доступно в _tmpl (роль для меню)
            return admin
    # маскируемся под обычный 404 — никаких признаков существования админки
    raise HTTPException(status_code=404, detail="Not Found")


async def require_role_admin(admin: AdminUser = Depends(require_admin)) -> AdminUser:
    """Разделы только для роли admin (API-ключи). Оператору — 404 (раздел скрыт)."""
    if admin.role != "admin":
        raise HTTPException(status_code=404, detail="Not Found")
    return admin


# Основной роутер админки — закрыт гардом целиком.
router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])
# Роутер входа/выхода — БЕЗ гарда (иначе на страницу логина не попасть).
auth_router = APIRouter(tags=["admin-auth"])
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["intcomma"] = lambda v: f"{int(v):,}".replace(",", " ")
templates.env.filters["abs"] = abs
templates.env.globals["enumerate"] = enumerate

MEDIA_DIR = "media"
_WEBP_QUALITY = 85
_MAX_DIM = 1920  # px, длинная сторона


def _to_webp(data: bytes) -> bytes:
    """Конвертирует изображение в WebP, ресайзит если больше _MAX_DIM."""
    img = PILImage.open(io.BytesIO(data))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_DIM:
        ratio = _MAX_DIM / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="webp", quality=_WEBP_QUALITY, method=4)
    return buf.getvalue()

STATUS_LABELS = {
    "new": "Новый", "accepted": "Принят", "delivery": "В пути",
    "done": "Выполнен", "canceled": "Отменён",
}
PAY_STATUS = {"pending": "Ожидает", "paid": "Оплачен", "refunded": "Возврат"}
PAY_METHOD = {"online": "Онлайн", "terminal": "Картой курьеру", "cash": "Наличные"}
TYPE_LABELS = {"discount": "Скидка", "cashback": "Кешбэк", "referral": "Реферальный"}
WEEKDAYS   = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}

PAGE_SIZE = 25

_CTX = dict(
    STATUS_LABELS=STATUS_LABELS, PAY_STATUS=PAY_STATUS,
    PAY_METHOD=PAY_METHOD, TYPE_LABELS=TYPE_LABELS, WEEKDAYS=WEEKDAYS,
)


def _r(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _tmpl(name: str, request: Request, ctx: dict):
    admin = getattr(request.state, "admin", None)
    role = admin.role if admin else None
    return templates.TemplateResponse(name, {"request": request, "role": role, **_CTX, **ctx})


# ─── Вход / выход (без гарда) ─────────────────────────────────────────────────

@auth_router.get("/login/{secret}", response_class=HTMLResponse)
async def admin_login_form(secret: str, request: Request):
    """Страница входа доступна ТОЛЬКО по секретному префиксу; иначе 404."""
    if not admin_auth.secret_matches(secret):
        raise HTTPException(status_code=404, detail="Not Found")
    return _tmpl("admin/login.html", request, {"secret": secret, "error": None})


@auth_router.post("/login/{secret}", response_class=HTMLResponse)
async def admin_login_submit(
    secret: str,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if not admin_auth.secret_matches(secret):
        raise HTTPException(status_code=404, detail="Not Found")

    ip = audit.client_ip(request)
    # Rate-limit перебора пароля: не больше 10 попыток за 5 минут с одного IP.
    if audit.rate_limited(f"admin_login:{ip}", limit=10, window_sec=300):
        await audit.log_event(db, request, "admin_login", "blocked", "rate limit IP")
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")

    admin = (
        await db.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if not admin or not admin.is_active or not admin_auth.verify_password(password, admin.password_hash):
        await audit.log_event(db, request, "admin_login", "fail", f"user={username[:40]}")
        return _tmpl("admin/login.html", request,
                     {"secret": secret, "error": "Неверный логин или пароль"})

    admin.last_login = datetime.utcnow()
    await db.commit()
    await audit.log_event(db, request, "admin_login", "ok", f"user={username[:40]}", user_id=admin.id)

    resp = _r("/admin")
    resp.set_cookie(
        key=admin_auth.ADMIN_COOKIE,
        value=admin_auth.create_admin_session(admin.id),
        httponly=True,
        max_age=admin_auth.ADMIN_SESSION_DAYS * 86400,
        samesite="lax",
        secure=False,   # True на проде с HTTPS
    )
    return resp


@auth_router.get("/logout")
async def admin_logout():
    resp = _r(f"/admin/login/{settings.ADMIN_URL_SECRET}" if settings.ADMIN_URL_SECRET else "/")
    resp.delete_cookie(admin_auth.ADMIN_COOKIE)
    return resp


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    today = date.today()

    orders_today = (await db.execute(
        select(func.count(Order.id)).where(func.date(Order.created_at) == today)
    )).scalar() or 0

    revenue_today = (await db.execute(
        select(func.coalesce(func.sum(Order.total_amount), 0))
        .where(func.date(Order.created_at) == today,
               Order.status != OrderStatus.canceled)
    )).scalar() or 0

    orders_total = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    users_total  = (await db.execute(select(func.count(User.id)))).scalar() or 0

    recent = (await db.execute(
        select(Order).order_by(desc(Order.id)).limit(10)
    )).scalars().all()

    return _tmpl("admin/dashboard.html", request, {
        "active": "dashboard",
        "stats": {
            "orders_today": orders_today,
            "revenue_today": revenue_today,
            "orders_total": orders_total,
            "users_total": users_total,
        },
        "recent_orders": recent,
    })


# ─── Orders ───────────────────────────────────────────────────────────────────

@router.get("/orders", response_class=HTMLResponse)
async def admin_orders(
    request: Request,
    q: str = "", status: str = "", date_filter: str = "", page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Order)
    if q:
        if q.startswith("#") and q[1:].isdigit():
            stmt = stmt.where(Order.id == int(q[1:]))
        else:
            stmt = stmt.where(Order.phone.ilike(f"%{q}%"))
    if status:
        try:
            stmt = stmt.where(Order.status == OrderStatus(status))
        except ValueError:
            pass
    if date_filter:
        try:
            d = date.fromisoformat(date_filter)
            stmt = stmt.where(Order.delivery_date == d)
        except ValueError:
            pass

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    orders = (await db.execute(
        stmt.order_by(desc(Order.id)).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    return _tmpl("admin/orders.html", request, {
        "active": "orders", "orders": orders, "q": q,
        "status_filter": status, "date_filter": date_filter,
        "statuses": [s.value for s in OrderStatus],
        "page": page, "has_next": page * PAGE_SIZE < total, "total": total,
        "msg": request.query_params.get("msg"),
    })


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def admin_order_detail(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404)
    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
    return _tmpl("admin/order_detail.html", request, {
        "active": "orders", "order": order, "items": items,
        "msg": request.query_params.get("msg"),
    })


@router.get("/orders/{order_id}/print", response_class=HTMLResponse)
async def admin_order_print(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """Лист заказа для печати (сборка + доставка): чистая страница, авто-печать."""
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404)
    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
    user = None
    if order.user_id:
        user = (await db.execute(select(User).where(User.id == order.user_id))).scalar_one_or_none()
    goods_total = sum(i.line_total for i in items)
    return _tmpl("admin/order_print.html", request, {
        "order": order, "items": items, "user": user, "goods_total": goods_total,
    })


@router.post("/orders/{order_id}/status")
async def admin_order_set_status(
    order_id: int,
    status: str = Form(...),
    cancel_reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404)
    try:
        order.status = OrderStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный статус")
    if cancel_reason:
        order.cancel_reason = cancel_reason
    await db.commit()
    try:
        await notify_order_status(db, order)
    except Exception as exc:
        logger.warning("[notify] %s", exc)
    return _r(f"/admin/orders/{order_id}?msg=Статус+обновлён")


# ─── Categories ───────────────────────────────────────────────────────────────

@router.get("/categories", response_class=HTMLResponse)
async def admin_categories(request: Request, db: AsyncSession = Depends(get_db)):
    cats = (await db.execute(select(Category).order_by(Category.sort_order, Category.id))).scalars().all()
    return _tmpl("admin/categories.html", request, {
        "active": "categories", "categories": cats,
        "msg": request.query_params.get("msg"),
    })


class CategoryIn(BaseModel):
    name: str
    slug: str
    icon: Optional[str] = None
    show_icon: bool = True
    sort_order: int = 0
    is_visible: bool = True


@router.post("/categories")
async def admin_category_create(payload: CategoryIn, db: AsyncSession = Depends(get_db)):
    c = Category(
        name=payload.name, slug=payload.slug,
        icon=payload.icon, show_icon=payload.show_icon,
        sort_order=payload.sort_order,
        is_visible=payload.is_visible,
    )
    db.add(c)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Slug уже занят")
    return {"ok": True, "id": c.id}


@router.put("/categories/{cat_id}")
async def admin_category_update(cat_id: int, payload: CategoryIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Category).where(Category.id == cat_id))
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404)
    c.name = payload.name
    c.slug = payload.slug
    c.icon = payload.icon
    c.show_icon = payload.show_icon
    c.sort_order = payload.sort_order
    c.is_visible = payload.is_visible
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Slug уже занят")
    return {"ok": True}


@router.post("/categories/{cat_id}/icon")
async def admin_category_icon(cat_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Загрузка SVG-иконки категории. Файл кладётся в media/, путь пишется в icon."""
    res = await db.execute(select(Category).where(Category.id == cat_id))
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404)

    name = (file.filename or "").lower()
    content = await file.read()
    is_svg = name.endswith(".svg") or b"<svg" in content[:512].lower()
    if not is_svg:
        raise HTTPException(status_code=400, detail="Загрузите файл в формате SVG")
    if len(content) > 256 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 256 КБ)")

    os.makedirs(MEDIA_DIR, exist_ok=True)
    fname = f"cat_{cat_id}_{uuid.uuid4().hex[:8]}.svg"
    with open(os.path.join(MEDIA_DIR, fname), "wb") as f:
        f.write(content)
    c.icon = f"/media/{fname}"
    await db.commit()
    return {"ok": True, "icon": c.icon}


@router.post("/categories/reorder")
async def admin_categories_reorder(request: Request, db: AsyncSession = Depends(get_db)):
    """Принимает [{id, sort_order}, ...] и обновляет порядок."""
    data = await request.json()
    for item in data:
        await db.execute(
            update(Category)
            .where(Category.id == item["id"])
            .values(sort_order=item["sort_order"])
        )
    await db.commit()
    return {"ok": True}


@router.delete("/categories/{cat_id}")
async def admin_category_delete(cat_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Category).where(Category.id == cat_id))
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404)
    await db.delete(c)
    await db.commit()
    return {"ok": True}


# ─── Products ─────────────────────────────────────────────────────────────────

@router.get("/products", response_class=HTMLResponse)
async def admin_products(
    request: Request, q: str = "", cat: str = "", page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.images))
        .where(Product.is_deleted == False)
    )
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    if cat:
        try:
            stmt = stmt.where(Product.category_id == int(cat))
        except ValueError:
            pass

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    products = (await db.execute(
        stmt.order_by(Product.sort_order, Product.id)
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()

    return _tmpl("admin/products.html", request, {
        "active": "products", "products": products, "categories": cats,
        "q": q, "cat_filter": cat,
        "page": page, "has_next": page * PAGE_SIZE < total, "total": total,
        "msg": request.query_params.get("msg"),
    })


async def _resolve_group(db: AsyncSession, group_id: str, group_new: str) -> Optional[int]:
    """Определяет group_id для товара по данным формы.
      group_new — название новой группы (если задано, создаём ProductGroup);
      group_id  — id существующей группы ('' или '0' → без группы)."""
    group_new = (group_new or "").strip()
    if group_new:
        slug_base = _slugify_ru(group_new)
        # переиспользуем существующую группу с таким slug_base, иначе создаём
        g = (await db.execute(
            select(ProductGroup).where(ProductGroup.slug_base == slug_base)
        )).scalar_one_or_none()
        if not g:
            g = ProductGroup(name=group_new, slug_base=slug_base)
            db.add(g)
            await db.flush()
        return g.id
    if group_id and group_id not in ("0", ""):
        return int(group_id)
    return None


def _slugify_ru(text: str) -> str:
    import re, unicodedata
    table = {
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z",
        "и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
        "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch",
        "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
    }
    s = text.lower().strip()
    s = "".join(table.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "group"


async def _set_group_default(db: AsyncSession, group_id: Optional[int], product_id: int, is_default: bool):
    """Назначает товар вариантом по умолчанию для группы, если is_default."""
    if not group_id or not is_default:
        return
    g = (await db.execute(select(ProductGroup).where(ProductGroup.id == group_id))).scalar_one_or_none()
    if g:
        g.default_product_id = product_id


@router.get("/products/new", response_class=HTMLResponse)
async def admin_product_new(request: Request, db: AsyncSession = Depends(get_db)):
    cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()
    groups = (await db.execute(select(ProductGroup).order_by(ProductGroup.name))).scalars().all()
    return _tmpl("admin/product_form.html", request, {
        "active": "products", "product": None, "categories": cats, "groups": groups, "error": None,
    })


@router.post("/products/new")
async def admin_product_create(
    request: Request,
    name: str = Form(...), slug: str = Form(...), category_id: str = Form(""),
    price: int = Form(...),
    description: str = Form(""), composition: str = Form(""),
    kcal: str = Form(""), protein: str = Form(""), fat: str = Form(""), carbs: str = Form(""),
    weight: str = Form(""), sort_order: int = Form(0),
    group_id: str = Form(""), group_new: str = Form(""),
    variant_label: str = Form(""), is_default: str = Form(""),
    is_visible: str = Form(""), label_popular: str = Form(""), label_new: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()
    groups = (await db.execute(select(ProductGroup).order_by(ProductGroup.name))).scalars().all()
    if not category_id:
        return _tmpl("admin/product_form.html", request, {
            "active": "products", "product": None, "categories": cats, "groups": groups,
            "error": "Выберите категорию",
        })
    # проверяем уникальность slug
    existing = (await db.execute(select(Product).where(Product.slug == slug))).scalar_one_or_none()
    if existing:
        return _tmpl("admin/product_form.html", request, {
            "active": "products", "product": None, "categories": cats, "groups": groups,
            "error": f"Slug «{slug}» уже занят",
        })

    gid = await _resolve_group(db, group_id, group_new)
    p = Product(
        name=name, slug=slug,
        category_id=int(category_id) if category_id else None,
        price=price,
        description=description or None,
        composition=composition or None,
        kcal=int(kcal) if kcal else None,
        protein=float(protein) if protein else None,
        fat=float(fat) if fat else None,
        carbs=float(carbs) if carbs else None,
        weight=weight or None,
        sort_order=sort_order,
        group_id=gid,
        variant_label=(variant_label.strip() or None) if gid else None,
        is_visible=bool(is_visible),
        label_popular=bool(label_popular),
        label_new=bool(label_new),
    )
    db.add(p)
    await db.flush()
    await _set_group_default(db, gid, p.id, bool(is_default))
    await _save_images(db, p.id, images)
    await db.commit()
    return _r(f"/admin/products?msg=Товар+создан")


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def admin_product_edit(request: Request, product_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Product).options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id == product_id)
    )
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404)
    cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()
    groups = (await db.execute(select(ProductGroup).order_by(ProductGroup.name))).scalars().all()
    # является ли товар вариантом по умолчанию своей группы
    is_default = False
    if product.group_id:
        g = (await db.execute(select(ProductGroup).where(ProductGroup.id == product.group_id))).scalar_one_or_none()
        is_default = bool(g and g.default_product_id == product.id)
    return _tmpl("admin/product_form.html", request, {
        "active": "products", "product": product, "categories": cats,
        "groups": groups, "is_default": is_default, "error": None,
    })


@router.post("/products/{product_id}/edit")
async def admin_product_update(
    request: Request, product_id: int,
    name: str = Form(...), slug: str = Form(...), category_id: str = Form(""),
    price: int = Form(...), description: str = Form(""), composition: str = Form(""),
    kcal: str = Form(""), protein: str = Form(""), fat: str = Form(""), carbs: str = Form(""),
    weight: str = Form(""), sort_order: int = Form(0),
    group_id: str = Form(""), group_new: str = Form(""),
    variant_label: str = Form(""), is_default: str = Form(""),
    is_visible: str = Form(""), label_popular: str = Form(""), label_new: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Product).options(selectinload(Product.images)).where(Product.id == product_id)
    )
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404)

    if not category_id:
        cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()
        groups = (await db.execute(select(ProductGroup).order_by(ProductGroup.name))).scalars().all()
        return _tmpl("admin/product_form.html", request, {
            "active": "products", "product": product, "categories": cats, "groups": groups,
            "error": "Выберите категорию",
        })

    # проверяем slug на уникальность (исключая себя)
    dup = (await db.execute(
        select(Product).where(Product.slug == slug, Product.id != product_id)
    )).scalar_one_or_none()
    if dup:
        cats = (await db.execute(select(Category).order_by(Category.sort_order))).scalars().all()
        groups = (await db.execute(select(ProductGroup).order_by(ProductGroup.name))).scalars().all()
        return _tmpl("admin/product_form.html", request, {
            "active": "products", "product": product, "categories": cats, "groups": groups,
            "error": f"Slug «{slug}» уже занят",
        })

    gid = await _resolve_group(db, group_id, group_new)
    product.name        = name
    product.slug        = slug
    product.category_id = int(category_id) if category_id else None
    product.price       = price
    product.description = description or None
    product.composition = composition or None
    product.kcal        = int(kcal) if kcal else None
    product.protein     = float(protein) if protein else None
    product.fat         = float(fat) if fat else None
    product.carbs       = float(carbs) if carbs else None
    product.weight      = weight or None
    product.sort_order  = sort_order
    product.group_id    = gid
    product.variant_label = (variant_label.strip() or None) if gid else None
    product.is_visible  = bool(is_visible)
    product.label_popular = bool(label_popular)
    product.label_new   = bool(label_new)

    await _set_group_default(db, gid, product.id, bool(is_default))
    await _save_images(db, product_id, images)
    await db.commit()
    return _r(f"/admin/products/{product_id}/edit?msg=Сохранено")


@router.get("/products/{product_id}/delete-image/{image_id}")
async def admin_delete_image(product_id: int, image_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ProductImage).where(ProductImage.id == image_id, ProductImage.product_id == product_id))
    img = res.scalar_one_or_none()
    if img:
        # удаляем файл
        filepath = os.path.join(MEDIA_DIR, os.path.basename(img.url))
        if os.path.exists(filepath):
            os.remove(filepath)
        await db.delete(img)
        await db.commit()
    return _r(f"/admin/products/{product_id}/edit")


@router.post("/products/{product_id}/toggle")
async def admin_product_toggle(product_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Product).where(Product.id == product_id))
    p = res.scalar_one_or_none()
    if p:
        p.is_visible = not p.is_visible
        await db.commit()
    return _r("/admin/products")


async def _save_images(db: AsyncSession, product_id: int, uploads: list[UploadFile]):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    for upload in uploads:
        if not upload.filename or upload.size == 0:
            continue
        content = await upload.read()
        try:
            content = _to_webp(content)
            name = f"{uuid.uuid4().hex}.webp"
        except Exception:
            ext  = os.path.splitext(upload.filename)[1].lower() or ".jpg"
            name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(MEDIA_DIR, name)
        with open(path, "wb") as f:
            f.write(content)
        max_ord = (await db.execute(
            select(func.coalesce(func.max(ProductImage.sort_order), -1))
            .where(ProductImage.product_id == product_id)
        )).scalar() or 0
        db.add(ProductImage(product_id=product_id, url=name, sort_order=max_ord + 1))


# ─── Users ────────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request, q: str = "", page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.phone.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    users_raw = (await db.execute(
        stmt.order_by(desc(User.id)).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    # обогащаем счётчиками
    user_ids = [u.id for u in users_raw]
    order_counts: dict[int, int] = {}
    if user_ids:
        rows = (await db.execute(
            select(Order.user_id, func.count(Order.id))
            .where(Order.user_id.in_(user_ids))
            .group_by(Order.user_id)
        )).all()
        order_counts = {r[0]: r[1] for r in rows}

    cashback_map: dict[int, int] = {}
    if user_ids:
        rows = (await db.execute(
            select(CashbackAccount.user_id, CashbackAccount.balance)
            .where(CashbackAccount.user_id.in_(user_ids))
        )).all()
        cashback_map = {r[0]: r[1] for r in rows}

    class _U:
        pass

    enriched = []
    for u in users_raw:
        obj = u
        obj.orders_count = order_counts.get(u.id, 0)
        obj.cashback     = cashback_map.get(u.id, 0)
        enriched.append(obj)

    return _tmpl("admin/users.html", request, {
        "active": "users", "users": enriched, "q": q,
        "page": page, "has_next": page * PAGE_SIZE < total, "total": total,
        "msg": request.query_params.get("msg"),
    })


@router.post("/users/{user_id}/block")
async def admin_user_block(user_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if u:
        u.is_blocked = not u.is_blocked
        await db.commit()
    return _r("/admin/users")


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404)
    orders = (await db.execute(
        select(Order).where(Order.user_id == user_id).order_by(desc(Order.id))
    )).scalars().all()
    cb_acc = (await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user_id)
    )).scalar_one_or_none()
    cb_txs = (await db.execute(
        select(CashbackTransaction).where(CashbackTransaction.user_id == user_id)
        .order_by(desc(CashbackTransaction.id)).limit(50)
    )).scalars().all()
    return _tmpl("admin/user_detail.html", request, {
        "active": "users", "u": u,
        "orders": orders, "cb_acc": cb_acc, "cb_txs": cb_txs,
        "msg": request.query_params.get("msg"),
    })


@router.post("/users/{user_id}/cashback")
async def admin_user_cashback(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    form = await request.form()
    amount = int(form.get("amount", 0))
    comment = str(form.get("comment", "Ручная корректировка администратором"))
    if amount == 0:
        return _r(f"/admin/users/{user_id}?msg=Сумма+не+может+быть+0")
    cb_acc = (await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user_id)
    )).scalar_one_or_none()
    if not cb_acc:
        cb_acc = CashbackAccount(user_id=user_id, balance=0)
        db.add(cb_acc)
    cb_acc.balance = max(0, cb_acc.balance + amount)
    tx_type = "manual"
    db.add(CashbackTransaction(
        user_id=user_id, type=tx_type, amount=amount, comment=comment,
    ))
    await db.commit()
    return _r(f"/admin/users/{user_id}?msg=Кешбэк+обновлён")


# ─── Promocodes ───────────────────────────────────────────────────────────────

@router.get("/promocodes", response_class=HTMLResponse)
async def admin_promocodes(request: Request, db: AsyncSession = Depends(get_db)):
    promos = (await db.execute(select(Promocode).order_by(desc(Promocode.id)))).scalars().all()
    promos_json = [
        {
            "id": p.id, "code": p.code, "type": p.type.value,
            "discount_percent": p.discount_percent,
            "cashback_buyer_pct": p.cashback_buyer_pct,
            "min_order_amount": p.min_order_amount,
            "usage_limit": p.usage_limit, "usage_count": p.usage_count,
            "valid_from": p.valid_from.isoformat() if p.valid_from else None,
            "valid_until": p.valid_until.isoformat() if p.valid_until else None,
            "is_active": p.is_active,
        }
        for p in promos
    ]
    return _tmpl("admin/promocodes.html", request, {
        "active": "promocodes", "promos": promos, "promos_json": promos_json,
        "msg": request.query_params.get("msg"),
    })


class PromoIn(BaseModel):
    code: str
    type: str = "discount"
    discount_percent: Optional[int] = None
    cashback_buyer_pct: Optional[int] = None
    min_order_amount: Optional[int] = None
    usage_limit: Optional[int] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    is_active: bool = True


@router.post("/promocodes")
async def admin_promo_create(payload: PromoIn, db: AsyncSession = Depends(get_db)):
    p = Promocode(
        code=payload.code.upper().strip(),
        type=PromoType(payload.type),
        discount_percent=payload.discount_percent,
        cashback_buyer_pct=payload.cashback_buyer_pct,
        min_order_amount=payload.min_order_amount,
        usage_limit=payload.usage_limit,
        valid_from=datetime.fromisoformat(payload.valid_from) if payload.valid_from else datetime.utcnow(),
        valid_until=datetime.fromisoformat(payload.valid_until) if payload.valid_until else None,
        is_active=payload.is_active,
    )
    db.add(p)
    await db.commit()
    return {"ok": True}


@router.put("/promocodes/{promo_id}")
async def admin_promo_update(promo_id: int, payload: PromoIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Promocode).where(Promocode.id == promo_id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404)
    p.code               = payload.code.upper().strip()
    p.type               = PromoType(payload.type)
    p.discount_percent   = payload.discount_percent
    p.cashback_buyer_pct = payload.cashback_buyer_pct
    p.min_order_amount   = payload.min_order_amount
    p.usage_limit        = payload.usage_limit
    p.valid_from         = datetime.fromisoformat(payload.valid_from) if payload.valid_from else p.valid_from
    p.valid_until        = datetime.fromisoformat(payload.valid_until) if payload.valid_until else None
    p.is_active          = payload.is_active
    await db.commit()
    return {"ok": True}


@router.post("/promocodes/{promo_id}/toggle")
async def admin_promo_toggle(promo_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Promocode).where(Promocode.id == promo_id))
    p = res.scalar_one_or_none()
    if p:
        p.is_active = not p.is_active
        await db.commit()
    return _r("/admin/promocodes")


# ─── Delivery Zones ───────────────────────────────────────────────────────────

@router.get("/zones", response_class=HTMLResponse)
async def admin_zones(request: Request, db: AsyncSession = Depends(get_db)):
    zones = (await db.execute(select(DeliveryZone).order_by(desc(DeliveryZone.priority)))).scalars().all()
    zones_json = [
        {
            "id": z.id, "name": z.name, "color": z.color,
            "delivery_price": z.delivery_price,
            "free_delivery_from": z.free_delivery_from,
            "min_order_sum": z.min_order_sum,
            "priority": z.priority, "is_active": z.is_active,
            "polygon": z.polygon,
        }
        for z in zones
    ]
    return _tmpl("admin/zones.html", request, {
        "active": "zones", "zones": zones, "zones_json": zones_json,
        "msg": request.query_params.get("msg"),
    })


class ZoneIn(BaseModel):
    name: str
    color: str = "#3388ff"
    delivery_price: int = 0
    free_delivery_from: Optional[int] = None
    min_order_sum: Optional[int] = None
    priority: int = 0
    is_active: bool = True


@router.post("/zones")
async def admin_zone_create(payload: ZoneIn, db: AsyncSession = Depends(get_db)):
    z = DeliveryZone(
        name=payload.name, color=payload.color,
        delivery_price=payload.delivery_price,
        free_delivery_from=payload.free_delivery_from,
        min_order_sum=payload.min_order_sum,
        priority=payload.priority, is_active=payload.is_active,
        polygon={"type": "Polygon", "coordinates": [[]]},
    )
    db.add(z)
    await db.commit()
    return {"ok": True, "id": z.id}


@router.put("/zones/{zone_id}")
async def admin_zone_update(zone_id: int, payload: ZoneIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DeliveryZone).where(DeliveryZone.id == zone_id))
    z = res.scalar_one_or_none()
    if not z:
        raise HTTPException(status_code=404)
    z.name               = payload.name
    z.color              = payload.color
    z.delivery_price     = payload.delivery_price
    z.free_delivery_from = payload.free_delivery_from
    z.min_order_sum      = payload.min_order_sum
    z.priority           = payload.priority
    z.is_active          = payload.is_active
    await db.commit()
    return {"ok": True}


class ZonePolygon(BaseModel):
    polygon: dict


@router.put("/zones/{zone_id}/polygon")
async def admin_zone_polygon(zone_id: int, payload: ZonePolygon, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DeliveryZone).where(DeliveryZone.id == zone_id))
    z = res.scalar_one_or_none()
    if not z:
        raise HTTPException(status_code=404)
    z.polygon = payload.polygon
    await db.commit()
    return {"ok": True}


@router.delete("/zones/{zone_id}")
async def admin_zone_delete(zone_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DeliveryZone).where(DeliveryZone.id == zone_id))
    z = res.scalar_one_or_none()
    if not z:
        raise HTTPException(status_code=404)
    await db.delete(z)
    await db.commit()
    return {"ok": True}


@router.post("/zones/{zone_id}/toggle")
async def admin_zone_toggle(zone_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DeliveryZone).where(DeliveryZone.id == zone_id))
    z = res.scalar_one_or_none()
    if z:
        z.is_active = not z.is_active
        await db.commit()
    return _r("/admin/zones")


# ─── Schedule ─────────────────────────────────────────────────────────────────

@router.get("/schedule", response_class=HTMLResponse)
async def admin_schedule(request: Request, db: AsyncSession = Depends(get_db)):
    entries = (await db.execute(select(ScheduleEntry).order_by(ScheduleEntry.id))).scalars().all()
    entries_json = [
        {
            "id": e.id, "entry_type": e.entry_type.value,
            "weekday": e.weekday,
            "specific_date": e.specific_date.isoformat() if e.specific_date else None,
            "delivery_start": e.delivery_start.strftime("%H:%M"),
            "delivery_end": e.delivery_end.strftime("%H:%M"),
            "slot_interval_min": e.slot_interval_min,
            "deadline_days_before": e.deadline_days_before,
            "deadline_time": e.deadline_time.strftime("%H:%M"),
            "is_active": e.is_active,
        }
        for e in entries
    ]
    return _tmpl("admin/schedule.html", request, {
        "active": "schedule", "entries": entries, "entries_json": entries_json,
        "msg": request.query_params.get("msg"),
    })


class ScheduleIn(BaseModel):
    entry_type: str = "weekday"
    weekday: Optional[int] = None
    specific_date: Optional[str] = None
    delivery_start: str = "10:00"
    delivery_end: str = "20:00"
    slot_interval_min: int = 60
    deadline_days_before: int = 0       # 0/1/2/3
    deadline_time: str = "00:00"
    is_active: bool = True


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@router.post("/schedule")
async def admin_schedule_create(payload: ScheduleIn, db: AsyncSession = Depends(get_db)):
    e = ScheduleEntry(
        entry_type=ScheduleEntryType(payload.entry_type),
        weekday=payload.weekday,
        specific_date=date.fromisoformat(payload.specific_date) if payload.specific_date else None,
        delivery_start=_parse_time(payload.delivery_start),
        delivery_end=_parse_time(payload.delivery_end),
        slot_interval_min=payload.slot_interval_min,
        deadline_days_before=payload.deadline_days_before,
        deadline_time=_parse_time(payload.deadline_time),
        is_active=payload.is_active,
    )
    db.add(e)
    await db.commit()
    return {"ok": True}


@router.put("/schedule/{entry_id}")
async def admin_schedule_update(entry_id: int, payload: ScheduleIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ScheduleEntry).where(ScheduleEntry.id == entry_id))
    e = res.scalar_one_or_none()
    if not e:
        raise HTTPException(status_code=404)
    e.entry_type       = ScheduleEntryType(payload.entry_type)
    e.weekday          = payload.weekday
    e.specific_date    = date.fromisoformat(payload.specific_date) if payload.specific_date else None
    e.delivery_start   = _parse_time(payload.delivery_start)
    e.delivery_end     = _parse_time(payload.delivery_end)
    e.slot_interval_min = payload.slot_interval_min
    e.deadline_days_before = payload.deadline_days_before
    e.deadline_time    = _parse_time(payload.deadline_time)
    e.is_active        = payload.is_active
    await db.commit()
    return {"ok": True}


@router.post("/schedule/{entry_id}/toggle")
async def admin_schedule_toggle(entry_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ScheduleEntry).where(ScheduleEntry.id == entry_id))
    e = res.scalar_one_or_none()
    if e:
        e.is_active = not e.is_active
        await db.commit()
    return _r("/admin/schedule")


@router.post("/schedule/{entry_id}/delete")
async def admin_schedule_delete(entry_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ScheduleEntry).where(ScheduleEntry.id == entry_id))
    e = res.scalar_one_or_none()
    if e:
        await db.delete(e)
        await db.commit()
    return _r("/admin/schedule")


@router.get("/schedule/preview")
async def admin_schedule_preview(db: AsyncSession = Depends(get_db)):
    """Предпросмотр слотов на ближайшие 7 дней (ТЗ, раздел 18)."""
    from app.services.schedule_svc import generate_slots, now_tomsk
    from datetime import timedelta

    entries = (await db.execute(
        select(ScheduleEntry).where(ScheduleEntry.is_active == True)
    )).scalars().all()
    entries = list(entries)

    today = now_tomsk().date()
    out = []
    for delta in range(7):
        d = today + timedelta(days=delta)
        slots = generate_slots(d, entries)
        out.append({
            "date": d.isoformat(),
            "weekday": d.isoweekday(),
            "slots": [s["label"] for s in slots],
        })
    return {"days": out}


# ─── Settings ─────────────────────────────────────────────────────────────────

_CHECKBOX_KEYS = {"metrika_webvisor", "metrika_ecom", "smsru_test"}


@router.get("/audit", response_class=HTMLResponse)
async def admin_audit(
    request: Request,
    ip: str = "", action: str = "", status: str = "", page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """Журнал важных действий: вход/код/админка. Фильтры по IP/действию/статусу."""
    stmt = select(AuditEvent)
    if ip:
        stmt = stmt.where(AuditEvent.ip.ilike(f"%{ip}%"))
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if status:
        stmt = stmt.where(AuditEvent.status == status)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    events = (await db.execute(
        stmt.order_by(desc(AuditEvent.id)).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    # Топ IP по активности за последние 24 часа
    since = datetime.utcnow() - timedelta(hours=24)
    top_ip = (await db.execute(
        select(AuditEvent.ip, func.count().label("cnt"))
        .where(AuditEvent.created_at >= since)
        .group_by(AuditEvent.ip)
        .order_by(desc("cnt"))
        .limit(10)
    )).all()

    return _tmpl("admin/audit.html", request, {
        "active": "audit",
        "events": events,
        "total": total,
        "page": page,
        "pages": (total + PAGE_SIZE - 1) // PAGE_SIZE,
        "f_ip": ip, "f_action": action, "f_status": status,
        "top_ip": [(r[0], r[1]) for r in top_ip],
    })


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, db: AsyncSession = Depends(get_db)):
    settings_rows = (await db.execute(select(SiteSetting).order_by(SiteSetting.key))).scalars().all()
    return _tmpl("admin/settings.html", request, {
        "active": "settings",
        "settings": settings_rows,
        "settings_map": {s.key: s.value for s in settings_rows},
        "msg": request.query_params.get("msg"),
    })


@router.post("/settings")
async def admin_settings_save(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services.settings import invalidate_settings_cache
    form = await request.form()
    form_data = dict(form)
    # Чекбоксы не передаются если не отмечены — явно ставим "0"
    for key in _CHECKBOX_KEYS:
        if key not in form_data:
            form_data[key] = "0"
    existing = {s.key: s for s in (await db.execute(select(SiteSetting))).scalars().all()}
    for key, value in form_data.items():
        if key in existing:
            existing[key].value = str(value)
        else:
            db.add(SiteSetting(key=key, value=str(value)))
    await db.commit()
    invalidate_settings_cache()
    return _r("/admin/settings?msg=Настройки+сохранены")


async def _save_settings_form(request: Request, db: AsyncSession) -> None:
    """Общий сейв формы настроек в SiteSetting (чекбоксы → '0' если не отмечены)."""
    from app.services.settings import invalidate_settings_cache
    form = await request.form()
    form_data = dict(form)
    for key in _CHECKBOX_KEYS:
        if key not in form_data:
            form_data[key] = "0"
    existing = {s.key: s for s in (await db.execute(select(SiteSetting))).scalars().all()}
    for key, value in form_data.items():
        if key in existing:
            existing[key].value = str(value)
        else:
            db.add(SiteSetting(key=key, value=str(value)))
    await db.commit()
    invalidate_settings_cache()


@router.get("/api-settings", response_class=HTMLResponse)
async def admin_api_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_role_admin),   # только admin, оператору → 404
):
    settings_rows = (await db.execute(select(SiteSetting).order_by(SiteSetting.key))).scalars().all()
    return _tmpl("admin/api_settings.html", request, {
        "active": "api-settings",
        "settings_map": {s.key: s.value for s in settings_rows},
        "msg": request.query_params.get("msg"),
    })


@router.post("/api-settings")
async def admin_api_settings_save(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_role_admin),
):
    await _save_settings_form(request, db)
    return _r("/admin/api-settings?msg=Ключи+сохранены")


# ─── Stories ──────────────────────────────────────────────────────────────────

@router.get("/stories", response_class=HTMLResponse)
async def admin_stories(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Story).options(selectinload(Story.slides)).order_by(Story.sort_order, Story.id)
    )).scalars().all()
    # добавляем счётчик слайдов как атрибут (SQLAlchemy-объект, не словарь)
    for s in rows:
        s.slides_count = len(s.slides)
    return _tmpl("admin/stories.html", request, {
        "active": "stories", "stories": rows,
        "msg": request.query_params.get("msg"),
    })


@router.get("/stories/new", response_class=HTMLResponse)
async def admin_story_new(request: Request):
    return _tmpl("admin/story_form.html", request, {
        "active": "stories", "story": None, "slides_json": [], "error": None, "msg": None,
    })


@router.post("/stories/new")
async def admin_story_create(
    request: Request,
    title: str = Form(...),
    sort_order: int = Form(0),
    expires_at: str = Form(""),
    is_visible: str = Form(""),
    cover: UploadFile = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    cover_filename = await _save_single_image(cover)
    if not cover_filename:
        return _tmpl("admin/story_form.html", request, {
            "active": "stories", "story": None, "slides_json": [],
            "error": "Обложка обязательна", "msg": None,
        })
    exp = None
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
        except ValueError:
            pass
    s = Story(
        title=title, cover_image=cover_filename,
        sort_order=sort_order, is_visible=bool(is_visible), expires_at=exp,
    )
    db.add(s)
    await db.commit()
    return _r(f"/admin/stories/{s.id}/edit?msg=Сторис+создана")


@router.get("/stories/{story_id}/edit", response_class=HTMLResponse)
async def admin_story_edit(request: Request, story_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Story).options(selectinload(Story.slides)).where(Story.id == story_id)
    )
    story = res.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404)
    slides_json = [
        {
            "id": sl.id, "image_url": sl.image_url, "text": sl.text,
            "text_color": sl.text_color, "btn_label": sl.btn_label,
            "btn_url": sl.btn_url, "sort_order": sl.sort_order,
        }
        for sl in story.slides
    ]
    return _tmpl("admin/story_form.html", request, {
        "active": "stories", "story": story, "slides_json": slides_json,
        "error": request.query_params.get("err"), "msg": request.query_params.get("msg"),
    })


@router.post("/stories/{story_id}/edit")
async def admin_story_update(
    story_id: int,
    title: str = Form(...),
    sort_order: int = Form(0),
    expires_at: str = Form(""),
    is_visible: str = Form(""),
    cover: UploadFile = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Story).where(Story.id == story_id))
    story = res.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404)
    story.title      = title
    story.sort_order = sort_order
    story.is_visible = bool(is_visible)
    if expires_at:
        try:
            story.expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            pass
    else:
        story.expires_at = None
    new_cover = await _save_single_image(cover)
    if new_cover:
        story.cover_image = new_cover
    await db.commit()
    return _r(f"/admin/stories/{story_id}/edit?msg=Сохранено")


@router.post("/stories/{story_id}/toggle")
async def admin_story_toggle(story_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Story).where(Story.id == story_id))
    s = res.scalar_one_or_none()
    if s:
        s.is_visible = not s.is_visible
        await db.commit()
    return _r("/admin/stories")


@router.post("/stories/{story_id}/delete")
async def admin_story_delete(story_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Story).where(Story.id == story_id).options(selectinload(Story.slides))
    )
    s = res.scalar_one_or_none()
    if s:
        await db.delete(s)
        await db.commit()
    return _r("/admin/stories")


# ─── Story Slides ─────────────────────────────────────────────────────────────

@router.post("/stories/{story_id}/slides")
async def admin_slide_save(
    story_id: int,
    slide_id: str = Form(""),
    text: str = Form(""),
    text_color: str = Form("#ffffff"),
    sort_order: int = Form(0),
    btn_label: str = Form(""),
    btn_url: str = Form(""),
    image: UploadFile = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    new_image = await _save_single_image(image)

    if slide_id:
        # редактирование существующего
        res = await db.execute(
            select(StorySlide).where(StorySlide.id == int(slide_id), StorySlide.story_id == story_id)
        )
        sl = res.scalar_one_or_none()
        if sl:
            if new_image:
                sl.image_url = new_image
            sl.text       = text or None
            sl.text_color = text_color
            sl.sort_order = sort_order
            sl.btn_label  = btn_label or None
            sl.btn_url    = btn_url   or None
            await db.commit()
    else:
        # новый слайд
        if not new_image:
            return _r(f"/admin/stories/{story_id}/edit?err=Выберите+изображение+для+слайда")
        sl = StorySlide(
            story_id=story_id, image_url=new_image,
            text=text or None, text_color=text_color,
            sort_order=sort_order,
            btn_label=btn_label or None, btn_url=btn_url or None,
        )
        db.add(sl)
        await db.commit()

    return _r(f"/admin/stories/{story_id}/edit?msg=Слайд+сохранён")


@router.post("/stories/{story_id}/slides/{slide_id}/delete")
async def admin_slide_delete(story_id: int, slide_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(StorySlide).where(StorySlide.id == slide_id, StorySlide.story_id == story_id)
    )
    sl = res.scalar_one_or_none()
    if sl:
        await db.delete(sl)
        await db.commit()
    return _r(f"/admin/stories/{story_id}/edit")


async def _save_single_image(upload: UploadFile | None) -> str | None:
    if not upload or not upload.filename:
        return None
    content = await upload.read()
    if not content:
        return None
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        content = _to_webp(content)
        name = f"{uuid.uuid4().hex}.webp"
    except Exception:
        ext  = os.path.splitext(upload.filename)[1].lower() or ".jpg"
        name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(MEDIA_DIR, name), "wb") as f:
        f.write(content)
    return name


# ─── Cashback ─────────────────────────────────────────────────────────────────

@router.get("/cashback", response_class=HTMLResponse)
async def admin_cashback(
    request: Request, page: int = 1, tx_type: str = "",
    db: AsyncSession = Depends(get_db),
):
    PAGE_SIZE = 50

    # Общий баланс системы
    total_balance = (await db.execute(
        select(func.coalesce(func.sum(CashbackAccount.balance), 0))
    )).scalar() or 0

    # Статистика по типам
    stats_rows = (await db.execute(
        select(CashbackTransaction.type, func.sum(CashbackTransaction.amount))
        .group_by(CashbackTransaction.type)
    )).all()
    stats = {r[0]: r[1] for r in stats_rows}

    # Лог транзакций с пагинацией
    stmt = (
        select(CashbackTransaction)
        .options(selectinload(CashbackTransaction.user))
    )
    if tx_type:
        stmt = stmt.where(CashbackTransaction.type == tx_type)
    total_txs = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar() or 0
    txs = (await db.execute(
        stmt.order_by(desc(CashbackTransaction.id))
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    return _tmpl("admin/cashback.html", request, {
        "active": "cashback",
        "total_balance": total_balance,
        "stats": stats,
        "txs": txs,
        "tx_type": tx_type,
        "page": page,
        "has_next": page * PAGE_SIZE < total_txs,
        "total_txs": total_txs,
        "msg": request.query_params.get("msg"),
    })


@router.post("/cashback/withdraw/{user_id}")
async def admin_cashback_withdraw(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    amount = int(form.get("amount", 0))
    comment = str(form.get("comment", "Вывод средств"))
    if amount <= 0:
        return _r("/admin/cashback?msg=Сумма+должна+быть+положительной")
    cb_acc = (await db.execute(
        select(CashbackAccount).where(CashbackAccount.user_id == user_id)
    )).scalar_one_or_none()
    if not cb_acc or cb_acc.balance < amount:
        return _r("/admin/cashback?msg=Недостаточно+средств+на+балансе")
    cb_acc.balance -= amount
    db.add(CashbackTransaction(
        user_id=user_id, type=CashbackTxType.withdraw,
        amount=-amount, comment=comment,
    ))
    await db.commit()
    return _r(f"/admin/cashback?msg=Вывод+{amount}+₽+выполнен")


# ─── Reports ──────────────────────────────────────────────────────────────────

@router.get("/reports", response_class=HTMLResponse)
async def admin_reports(
    request: Request,
    period: str = "day",
    date_from: str = "",
    date_to: str = "",
    zone_id: str = "",
    payment: str = "",
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import cast, Date as SADate, Integer as SAInt, text

    today = date.today()
    try:
        df = date.fromisoformat(date_from) if date_from else date(today.year, today.month, 1)
        dt = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        df = date(today.year, today.month, 1)
        dt = today

    # Базовый фильтр заказов
    base = [
        Order.status != OrderStatus.canceled,
        Order.delivery_date >= df,
        Order.delivery_date <= dt,
    ]
    if zone_id:
        base.append(Order.zone_id == int(zone_id))
    if payment:
        base.append(Order.payment_method == payment)

    # Выручка по периодам
    if period == "month":
        trunc_expr = func.date_trunc("month", cast(Order.delivery_date, SADate))
    elif period == "week":
        trunc_expr = func.date_trunc("week", cast(Order.delivery_date, SADate))
    else:
        trunc_expr = cast(Order.delivery_date, SADate)

    revenue_rows = (await db.execute(
        select(trunc_expr.label("period"), func.sum(Order.total_amount).label("revenue"), func.count(Order.id).label("cnt"))
        .where(*base)
        .group_by(text("period"))
        .order_by(text("period"))
    )).all()

    # Топ товаров
    top_products = (await db.execute(
        select(OrderItem.product_name, func.sum(OrderItem.quantity).label("qty"), func.sum(OrderItem.line_total).label("total"))
        .join(Order, OrderItem.order_id == Order.id)
        .where(*base)
        .group_by(OrderItem.product_name)
        .order_by(desc("qty"))
        .limit(20)
    )).all()

    # Клиенты: новые vs повторные (упрощённо: заказы user_id не null)
    client_stats = (await db.execute(
        select(func.count(Order.id).label("total"), func.count(Order.user_id).label("auth"))
        .where(*base)
    )).one()
    avg_check = (await db.execute(
        select(func.avg(Order.total_amount)).where(*base)
    )).scalar() or 0

    # Промокоды
    promo_stats = (await db.execute(
        select(Promocode.code, func.count(Order.id).label("uses"), func.sum(Order.discount_amount).label("discount"))
        .join(Order, Order.promocode_id == Promocode.id)
        .where(*base)
        .group_by(Promocode.code)
        .order_by(desc("uses"))
    )).all()

    # По зонам
    zone_stats = (await db.execute(
        select(DeliveryZone.name, func.count(Order.id).label("cnt"), func.sum(Order.total_amount).label("revenue"))
        .join(Order, Order.zone_id == DeliveryZone.id)
        .where(*base)
        .group_by(DeliveryZone.name)
        .order_by(desc("revenue"))
    )).all()

    zones = (await db.execute(select(DeliveryZone).order_by(DeliveryZone.name))).scalars().all()

    return _tmpl("admin/reports.html", request, {
        "active": "reports",
        "period": period, "date_from": df.isoformat(), "date_to": dt.isoformat(),
        "zone_id": zone_id, "payment": payment,
        "revenue_rows": revenue_rows,
        "top_products": top_products,
        "client_stats": client_stats, "avg_check": int(avg_check),
        "promo_stats": promo_stats,
        "zone_stats": zone_stats,
        "zones": zones,
    })


@router.get("/reports/export")
async def admin_reports_export(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    zone_id: str = "",
    payment: str = "",
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    try:
        df = date.fromisoformat(date_from) if date_from else date(today.year, today.month, 1)
        dt = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        df = date(today.year, today.month, 1)
        dt = today

    base = [
        Order.status != OrderStatus.canceled,
        Order.delivery_date >= df,
        Order.delivery_date <= dt,
    ]
    if zone_id:
        base.append(Order.zone_id == int(zone_id))
    if payment:
        base.append(Order.payment_method == payment)

    orders = (await db.execute(
        select(Order).options(selectinload(Order.items))
        .where(*base).order_by(Order.delivery_date, Order.id)
    )).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Дата", "Телефон", "Адрес", "Статус", "Оплата", "Доставка ₽", "Скидка ₽", "Кешбэк списан ₽", "Итого ₽"])
    for o in orders:
        w.writerow([
            o.id, o.delivery_date, o.phone, o.address,
            o.status.value, o.payment_method.value,
            o.delivery_price, o.discount_amount, o.cashback_spent, o.total_amount,
        ])
    buf.seek(0)
    filename = f"vkusno_orders_{df}_{dt}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
