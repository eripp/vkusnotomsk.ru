import mimetypes

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

# В slim-образе Python неполная база mimetypes — webp может отдаваться как
# text/plain. Регистрируем явно (на проде статику отдаёт nginx).
mimetypes.add_type("image/webp", ".webp")

from app.routers import catalog, cart, auth, account, schedule, zones, stories
from app.routers.payment import router as payment_router, pages_router as payment_pages_router
from app.routers.orders import router as orders_router, pages_router as orders_pages_router
from app.routers.admin import router as admin_router, auth_router as admin_auth_router
from app.routers.tgbot import router as tgbot_router
from app.config import settings
from app.templates_env import templates

app = FastAPI(title="Vkusno Tomsk", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """404 → HTML-страница для обычных URL, JSON для API. Остальные коды — как есть."""
    wants_json = (
        request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "")
    )
    if exc.status_code == 404 and not wants_json:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.on_event("startup")
async def _seed_admin_user():
    """Засеваем учётку администратора из .env при запуске."""
    from app.services.admin_auth import seed_admin
    from app.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            await seed_admin(db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[admin] сидинг не выполнен: %s", exc)


@app.middleware("http")
async def inject_site_settings(request: Request, call_next):
    """Инжектит site_settings в request.state для всех шаблонов."""
    path = request.url.path
    if not path.startswith(("/static/", "/media/")):
        from app.services.settings import get_site_settings
        try:
            # Всегда передаём сессию: при живом кеше запрос к БД не выполняется
            # (отсекается по TTL), при устаревшем — кеш обновляется.
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                request.state.site_settings = await get_site_settings(db)
        except Exception:
            request.state.site_settings = {}
    else:
        request.state.site_settings = {}
    return await call_next(request)


@app.get("/robots.txt", response_class=Response)
async def robots():
    # Содержимое robots.txt можно переопределить в админке (настройка robots_txt).
    default = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Disallow: /checkout
Disallow: /pay/
Disallow: /order/

Sitemap: {settings.SITE_URL}/sitemap.xml
"""
    content = default
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        content = (cfg.get("robots_txt") or "").strip() or default
    except Exception:
        pass
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
async def sitemap(request: Request):
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import Category, Product

    async with AsyncSessionLocal() as db:
        cats = (await db.execute(
            select(Category).where(Category.is_visible == True)
        )).scalars().all()
        products = (await db.execute(
            select(Product).where(Product.is_visible == True, Product.is_deleted == False)
        )).scalars().all()

    base = settings.SITE_URL
    urls = [
        f"<url><loc>{base}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>",
    ]
    # статические контент-страницы
    for path, prio in (("/delivery", "0.5"), ("/contacts", "0.5"),
                       ("/offer", "0.3"), ("/privacy", "0.3")):
        urls.append(
            f"<url><loc>{base}{path}</loc>"
            f"<changefreq>monthly</changefreq><priority>{prio}</priority></url>"
        )
    for c in cats:
        urls.append(
            f"<url><loc>{base}/category/{c.slug}</loc>"
            f"<changefreq>daily</changefreq><priority>0.8</priority></url>"
        )
    for p in products:
        urls.append(
            f"<url><loc>{base}/product/{p.slug}</loc>"
            f"<changefreq>weekly</changefreq><priority>0.7</priority></url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@app.get("/feed", response_class=Response)
@app.get("/feed.xml", response_class=Response)
async def yml_feed():
    """YML-фид (Yandex Market Language) для Яндекс.Еды/Бизнеса и 2ГИС.

    Отдаёт весь видимый каталог: категории деревом + офферы с ценой, картинкой,
    весом и КБЖУ. Телефон и стоимость доставки для фида берутся из админ-настроек
    (feed_phone, feed_delivery_cost), значения по умолчанию — как в примере ТЗ.
    """
    from xml.sax.saxutils import escape, quoteattr
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import AsyncSessionLocal
    from app.models import Category, Product
    from app.services.settings import get_site_settings
    from app.services.schedule_svc import now_tomsk
    from app.routers.catalog import _media_url

    base = settings.SITE_URL.rstrip("/")

    async with AsyncSessionLocal() as db:
        cfg = await get_site_settings(db)
        cats = (await db.execute(
            select(Category).where(Category.is_visible == True).order_by(Category.sort_order)
        )).scalars().all()
        products = (await db.execute(
            select(Product)
            .where(Product.is_visible == True, Product.is_deleted == False, Product.price > 0)
            .order_by(Product.sort_order, Product.id)
            .options(selectinload(Product.images))
        )).scalars().all()

    phone = (cfg.get("feed_phone") or "").strip() or "+7 (3822) 713-100"
    try:
        delivery_cost = int(float(cfg.get("feed_delivery_cost") or 80))
    except (TypeError, ValueError):
        delivery_cost = 80

    # Категории отдаём только те, в которых есть офферы (пустые Яндекс/2ГИС не любят).
    cat_ids_with_offers = {p.category_id for p in products}
    cat_by_id = {c.id: c for c in cats}
    visible_cat_ids = {cid for cid in cat_ids_with_offers if cid in cat_by_id}

    cat_xml = "".join(
        f'<category id="{c.id}">{escape(c.name)}</category>'
        for c in cats if c.id in visible_cat_ids
    )

    def _abs_img(p: Product) -> str | None:
        imgs = sorted(p.images, key=lambda i: i.sort_order)
        if not imgs:
            return None
        url = _media_url(imgs[0].url)
        if not url:
            return None
        return url if url.startswith(("http://", "https://")) else base + url

    offers = []
    for p in products:
        if p.category_id not in visible_cat_ids:
            continue
        parts = [
            f'<offer id="{p.id}" available="true">',
            f"<url>{escape(base)}/product/{escape(p.slug)}</url>",
            f"<price>{p.price}</price>",
            '<currencyId>RUB</currencyId>',
            f"<categoryId>{p.category_id}</categoryId>",
        ]
        img = _abs_img(p)
        if img:
            parts.append(f"<picture>{escape(img)}</picture>")
        parts.append(f"<name>{escape(p.name)}</name>")
        desc = (p.meta_description or p.description or "").strip()
        if desc:
            parts.append(f"<description>{escape(desc)}</description>")
        if p.weight:
            parts.append(f"<param name=\"Вес\">{escape(str(p.weight))}</param>")
        for label, val, unit in (
            ("Калорийность", p.kcal, "ккал"),
            ("Белки", p.protein, "г"),
            ("Жиры", p.fat, "г"),
            ("Углеводы", p.carbs, "г"),
        ):
            if val is not None:
                num = f"{float(val):g}"
                parts.append(f'<param name={quoteattr(label)} unit={quoteattr(unit)}>{num}</param>')
        parts.append("</offer>")
        offers.append("".join(parts))

    date_attr = now_tomsk().strftime("%Y-%m-%d %H:%M")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<yml_catalog date="{date_attr}">'
        "<shop>"
        '<name>Фабрика "Вкусно"</name>'
        '<company>Фабрика "Вкусно"</company>'
        f"<url>{escape(base)}</url>"
        "<platform>vkusnotomsk.ru</platform>"
        "<version>1.0</version>"
        f"<phone>{escape(phone)}</phone>"
        '<currencies><currency id="RUB" rate="1"/></currencies>'
        f"<categories>{cat_xml}</categories>"
        f'<delivery-options><option cost="{delivery_cost}" days="0"/></delivery-options>'
        f"<offers>{''.join(offers)}</offers>"
        "</shop>"
        "</yml_catalog>"
    )
    return Response(content=xml, media_type="application/xml; charset=utf-8")


app.include_router(catalog.router)
app.include_router(cart.router, prefix="/api")
app.include_router(orders_pages_router)            # /checkout, /order/{id}
app.include_router(orders_router, prefix="/api")   # /api/orders, /api/address/detect-zone
app.include_router(auth.router, prefix="/api/auth")
app.include_router(account.router)
app.include_router(payment_pages_router)                    # /pay/{order_id}
app.include_router(payment_router, prefix="/api/payment")   # /api/payment/callback, /api/payment/status/{id}
app.include_router(schedule.router, prefix="/api/schedule")
app.include_router(zones.router, prefix="/api")
app.include_router(stories.router, prefix="/api")
app.include_router(admin_auth_router, prefix="/admin")   # /admin/login/{secret}, /logout — без гарда
app.include_router(admin_router, prefix="/admin")        # остальная админка — под гардом
app.include_router(tgbot_router, prefix="/api")    # /api/tg/webhook
