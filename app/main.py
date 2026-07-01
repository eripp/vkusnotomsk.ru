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
    content = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Disallow: /checkout
Disallow: /pay/
Disallow: /order/

Sitemap: {settings.SITE_URL}/sitemap.xml
"""
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
