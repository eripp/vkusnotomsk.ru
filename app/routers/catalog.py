from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from app.database import get_db
from app.models import Category, Product, ProductGroup, ProductImage, DeliveryZone
from app.routers.stories import get_active_stories
from app.templates_env import templates

router = APIRouter(tags=["catalog"])


async def _get_categories(db: AsyncSession) -> list[Category]:
    # только видимые категории, в которых есть хотя бы один доступный товар —
    # пустые категории не показываем в сайдбаре
    has_product = (
        select(Product.id)
        .where(
            Product.category_id == Category.id,
            Product.is_visible == True,
            Product.is_deleted == False,
        )
        .exists()
    )
    result = await db.execute(
        select(Category)
        .where(Category.is_visible == True, has_product)
        .order_by(Category.sort_order, Category.id)
    )
    return result.scalars().all()


async def _get_products(
    db: AsyncSession,
    category_slug: str | None = None,
    search: str | None = None,
    label: str | None = None,
) -> list[dict]:
    conditions = [Product.is_visible == True, Product.is_deleted == False]

    if category_slug:
        cat_result = await db.execute(select(Category).where(Category.slug == category_slug))
        cat = cat_result.scalar_one_or_none()
        if cat:
            conditions.append(Product.category_id == cat.id)
        else:
            return []

    if search:
        q = f"%{search}%"
        conditions.append(or_(Product.name.ilike(q), Product.description.ilike(q)))

    label_map = {
        "popular": Product.label_popular,
        "halal":   Product.label_halal,
        "post":    Product.label_post,
        "new":     Product.label_new,
        "kids":    Product.label_kids,
        "vegan":   Product.label_vegan,
    }
    if label and label in label_map:
        conditions.append(label_map[label] == True)

    result = await db.execute(
        select(Product).where(*conditions).order_by(Product.sort_order, Product.id)
    )
    products = result.scalars().all()

    # первое фото каждого товара
    product_ids = [p.id for p in products]
    images_map: dict[int, str] = {}
    if product_ids:
        imgs = await db.execute(
            select(ProductImage)
            .where(ProductImage.product_id.in_(product_ids))
            .order_by(ProductImage.product_id, ProductImage.sort_order)
        )
        for img in imgs.scalars().all():
            if img.product_id not in images_map:
                images_map[img.product_id] = img.url

    # карта групп → default_product_id (какой вариант показывать в каталоге)
    group_ids = {p.group_id for p in products if p.group_id is not None}
    default_map: dict[int, int] = {}
    group_counts: dict[int, int] = {}
    if group_ids:
        grows = await db.execute(
            select(ProductGroup.id, ProductGroup.default_product_id)
            .where(ProductGroup.id.in_(group_ids))
        )
        default_map = {gid: did for gid, did in grows.all() if did is not None}
        # полное число вариантов в группе (видимых, не удалённых) — для пометки
        # «ещё N вариантов»; считаем по всей группе, не по отфильтрованной выборке
        crows = await db.execute(
            select(Product.group_id, func.count(Product.id))
            .where(
                Product.group_id.in_(group_ids),
                Product.is_visible == True,
                Product.is_deleted == False,
            )
            .group_by(Product.group_id)
        )
        group_counts = {gid: cnt for gid, cnt in crows.all()}

    # Представитель группы: default_product_id, если он есть в выборке,
    # иначе первый по сортировке. Товары без группы — все.
    by_id = {p.id: p for p in products}
    group_repr: dict[int, int] = {}   # group_id → product_id-представитель
    for p in products:
        if p.group_id is None:
            continue
        if p.group_id not in group_repr:
            default_id = default_map.get(p.group_id)
            group_repr[p.group_id] = default_id if default_id in by_id else p.id

    output = []
    for p in products:
        if p.group_id is not None and group_repr.get(p.group_id) != p.id:
            continue
        d = _product_to_dict(p, images_map.get(p.id))
        d["variants_count"] = group_counts.get(p.group_id, 0) if p.group_id else 0
        output.append(d)
    return output


def _media_url(name: str | None) -> str | None:
    """Имя файла из БД → публичный путь. Внешние URL отдаём как есть."""
    if not name:
        return None
    if name.startswith(("http://", "https://", "/")):
        return name
    return f"/media/{name}"


def _product_to_dict(p: Product, cover_image: str | None = None) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "description": p.description,
        "composition": p.composition,
        "price": p.price,
        "weight": p.weight,
        "shelf_life": p.shelf_life,
        "storage_cond": p.storage_cond,
        "kcal": p.kcal,
        "protein": float(p.protein) if p.protein is not None else None,
        "fat": float(p.fat) if p.fat is not None else None,
        "carbs": float(p.carbs) if p.carbs is not None else None,
        "label_popular": p.label_popular,
        "label_halal":   p.label_halal,
        "label_post":    p.label_post,
        "label_new":     p.label_new,
        "label_kids":    p.label_kids,
        "label_vegan":   p.label_vegan,
        "group_id": p.group_id,
        "variant_label": p.variant_label,
        "category_id": p.category_id,
        "image": _media_url(cover_image),
    }


async def _get_product_detail(db: AsyncSession, slug: str) -> dict | None:
    result = await db.execute(
        select(Product).where(Product.slug == slug, Product.is_deleted == False)
    )
    p = result.scalar_one_or_none()
    if not p:
        return None

    # все фото товара
    imgs_result = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == p.id)
        .order_by(ProductImage.sort_order)
    )
    images = [_media_url(img.url) for img in imgs_result.scalars().all()]

    # варианты группы
    variants = []
    if p.group_id:
        vars_result = await db.execute(
            select(Product)
            .where(Product.group_id == p.group_id, Product.is_deleted == False, Product.is_visible == True)
            .order_by(Product.sort_order)
        )
        for v in vars_result.scalars().all():
            variants.append({"id": v.id, "slug": v.slug, "label": v.variant_label or v.name, "price": v.price})

    # рекомендации — другие товары той же категории
    recs_result = await db.execute(
        select(Product)
        .where(
            Product.category_id == p.category_id,
            Product.id != p.id,
            Product.is_visible == True,
            Product.is_deleted == False,
        )
        .order_by(Product.sort_order)
        .limit(8)
    )
    rec_products = recs_result.scalars().all()
    rec_ids = [r.id for r in rec_products]
    rec_images: dict[int, str] = {}
    if rec_ids:
        ri = await db.execute(
            select(ProductImage)
            .where(ProductImage.product_id.in_(rec_ids))
            .order_by(ProductImage.product_id, ProductImage.sort_order)
        )
        for img in ri.scalars().all():
            if img.product_id not in rec_images:
                rec_images[img.product_id] = img.url
    recommendations = [_product_to_dict(r, rec_images.get(r.id)) for r in rec_products]

    data = _product_to_dict(p, images[0] if images else None)
    data["images"] = images
    data["variants"] = variants
    data["recommendations"] = recommendations
    data["available"] = bool(p.is_visible)   # неактивный товар нельзя заказать
    return data


# ─── SSR страницы ─────────────────────────────────────────────────────────────

def _group_by_category(categories, products: list[dict]) -> list[dict]:
    """Группирует товары по категориям в порядке categories.
    Каждая группа: {"category": <Category>, "products": [...]}."""
    by_cat: dict[int, list[dict]] = {}
    for p in products:
        by_cat.setdefault(p["category_id"], []).append(p)
    groups = []
    for c in categories:
        items = by_cat.get(c.id)
        if items:
            groups.append({"category": c, "products": items})
    return groups


async def _free_delivery_from(db: AsyncSession):
    """Минимальный порог бесплатной доставки среди активных зон (для плашки)."""
    return (await db.execute(
        select(func.min(DeliveryZone.free_delivery_from))
        .where(DeliveryZone.is_active == True, DeliveryZone.free_delivery_from != None)
    )).scalar()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    categories = await _get_categories(db)
    products   = await _get_products(db)
    stories    = await get_active_stories(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": categories,
        "popular": [p for p in products if p["label_popular"]],
        "category_groups": _group_by_category(categories, products),
        "active_category": None,
        "stories": stories,
        "stories_json": stories,
        "free_delivery_from": await _free_delivery_from(db),
    })


@router.get("/delivery", response_class=HTMLResponse)
async def delivery_page(request: Request):
    """Страница «Условия доставки» — HTML редактируется в админке."""
    ss = getattr(request.state, "site_settings", {}) or {}
    return templates.TemplateResponse("content_page.html", {
        "request": request,
        "page_title": "Условия доставки",
        "page_html": ss.get("delivery_terms_html") or "",
    })


@router.get("/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request):
    """Страница «Контакты» — данные из настроек + карта Яндекс."""
    return templates.TemplateResponse("contacts_page.html", {"request": request})


@router.get("/category/{slug}", response_class=HTMLResponse)
async def category_page(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    categories = await _get_categories(db)
    products   = await _get_products(db)          # весь каталог — страница длинная, скроллим к якорю
    stories    = await get_active_stories(db)
    cat = next((c for c in categories if c.slug == slug), None)
    cat_name = cat.name if cat else slug
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": categories,
        "popular": [p for p in products if p["label_popular"]],
        "category_groups": _group_by_category(categories, products),
        "active_category": slug,
        "meta_title": f"{cat_name} — доставка по Томску · Вкусно",
        "meta_desc": f"Заказать {cat_name.lower()} с доставкой по Томску. Быстро, вкусно, горячо!",
        "stories": stories,
        "stories_json": stories,
        "free_delivery_from": await _free_delivery_from(db),
    })


@router.get("/product/{slug}", response_class=HTMLResponse)
async def product_page(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    categories = await _get_categories(db)
    products = await _get_products(db)
    product = await _get_product_detail(db, slug)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")

    # SEO мета
    meta_title = f"{product['name']} {product['weight'] or ''} — купить с доставкой · Вкусно Томск".strip()
    meta_desc  = product["description"] or meta_title

    stories = await get_active_stories(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": categories,
        "popular": [p for p in products if p["label_popular"]],
        "category_groups": _group_by_category(categories, products),
        "active_category": None,
        "open_product_slug": slug,
        "open_product": product,
        "meta_title": meta_title,
        "meta_desc": meta_desc,
        "stories": stories,
        "stories_json": stories,
        "free_delivery_from": await _free_delivery_from(db),
    })


# ─── API ──────────────────────────────────────────────────────────────────────

@router.get("/api/products")
async def api_products(
    category: str | None = None,
    search:   str | None = None,
    label:    str | None = None,
    db: AsyncSession = Depends(get_db),
):
    products = await _get_products(db, category_slug=category, search=search, label=label)
    return {"products": products, "total": len(products)}


@router.get("/api/categories")
async def api_categories(db: AsyncSession = Depends(get_db)):
    cats = await _get_categories(db)
    return [{"id": c.id, "name": c.name, "slug": c.slug, "icon": c.icon} for c in cats]


@router.get("/api/product/{slug}")
async def api_product(slug: str, db: AsyncSession = Depends(get_db)):
    product = await _get_product_detail(db, slug)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return product
