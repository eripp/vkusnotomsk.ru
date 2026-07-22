# CLAUDE.md

Гайд для работы с кодовой базой **Вкусно** — интернет-магазин готовой еды с доставкой (г. Томск). Полное ТЗ — в `ТЗ.txt`, история изменений — в `CHANGELOG.md`.

## Стек

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Pydantic v2
- **БД:** PostgreSQL 16, миграции — Alembic
- **Frontend:** SSR на Jinja2 + нативный Vanilla JS + нативный CSS. **Без** React/Vue, без сборщиков, без Tailwind/Bootstrap, без axios (только `fetch`). Принцип ТЗ: не тянуть библиотеку, если задача решается нативно.
- **Инфраструктура:** Docker Compose (`web` / `db` / `nginx` / `worker` / `certbot`)
- **Интеграции:** YooKassa (оплата), Dadata (адреса/геокодинг), Shapely (зоны доставки), Leaflet (карты), SMTP + Telegram Bot + MAX (уведомления)

## Команды

Приложение работает в Docker. Контейнер `web` запущен с `--reload` и бинд-маунтом репозитория (`.:/app`) — **изменения кода и шаблонов подхватываются без пересборки**.

```bash
docker compose up -d                 # поднять всё
docker compose logs web --tail 50    # логи приложения (трейсбэки 500 здесь)
docker compose exec web <cmd>        # выполнить что-либо в контейнере web

# Миграции (НЕ запускаются автоматически):
docker compose exec web alembic upgrade head
docker compose exec web alembic revision --autogenerate -m "описание"

# Наполнение каталога:
docker compose exec web python import_products.py   # импорт из product.csv (режим «заменить весь каталог»)
docker compose exec web python seed_catalog.py      # мини-каталог-заглушка
```

Локальный URL: `http://localhost:8000`. Статику в проде отдаёт nginx напрямую (`/static/`, `/media/`).

## Архитектура

```
app/
├── main.py            # FastAPI app, подключение роутеров, middleware site_settings, robots.txt, sitemap.xml
├── models.py          # все SQLAlchemy-модели (источник правды по схеме БД)
├── config.py          # Settings (pydantic-settings, читает .env)
├── database.py        # async engine, AsyncSessionLocal, get_db()
├── deps.py            # get_current_user / get_optional_user (JWT из httpOnly cookie)
├── templates_env.py   # общий Jinja2Templates (фильтр intcomma, глобал SITE_URL)
├── routers/
│   ├── catalog.py     # GET /, /category/{slug}, /product/{slug}, /api/products …
│   ├── cart.py        # /api/cart/validate (проверка доступности товаров)
│   ├── orders.py      # /checkout, /order/{id}, POST /api/orders, detect-zone, промокод
│   ├── auth.py        # OTP-авторизация, JWT
│   ├── account.py     # личный кабинет (профиль, заказы, кешбэк, уведомления)
│   ├── payment.py     # YooKassa: /pay/{id}, webhook /api/payment/callback
│   ├── schedule.py    # /api/schedule/available-days, /slots
│   ├── zones.py, stories.py, tgbot.py
│   └── admin/__init__.py   # ВСЯ админка одним файлом (заказы, товары, категории, юзеры, промокоды, зоны, расписание, сторис, кешбэк, отчёты, настройки)
├── services/          # бизнес-логика вне роутеров
│   ├── promo.py       # промокоды + кешбэк (earn/spend/balance)
│   ├── schedule_svc.py# генерация слотов с дедлайнами (UTC+7)
│   ├── notifications.py, yookassa.py, dadata.py, zones.py, settings.py, jwt.py
├── templates/         # Jinja2: base.html, index.html, checkout.html, admin/*, partials/*
├── static/            # css/main.css, js/main.js (каталог+модалка+сторис), js/cart.js
└── worker.py          # фоновый воркер (сейчас заглушка; уведомления шлются инлайн)
media/                 # загруженные фото (WebP), отдаёт nginx
```

## Важные соглашения

- **Цены — целые рубли (`int`)**, не копейки. Не менять.
- **Время:** в БД naive-`time`/`datetime`; вся логика расписания/дедлайнов — в **UTC+7 (Asia/Tomsk)**, см. `services/schedule_svc.py`.
- **Поля модели `Product`:** видимость — `is_visible`, метки — `label_popular`/`label_new`/`label_halal`/… , калории — `kcal`. Имена полей формы товара в админке совпадают с полями модели один в один (`is_visible`/`label_popular`/`label_new`/`kcal`) — отдельного маппинга в хендлере нет. У `Promocode`/`ScheduleEntry`/`DeliveryZone` поле включения называется `is_active` (это нормально, у них так в модели).
- **Изображения:** в БД хранится только **имя локального файла** (например `abc.webp`), не URL. Внешние ссылки недопустимы. Хелпер `_media_url()` в `catalog.py` строит публичный путь. Загружаемые фото конвертируются в WebP (Pillow) и кладутся в `media/`.
- **Каталог и группы вариантов:** в сетке показывается один товар от группы (схлопывание по `group_id`); все варианты видны в модалке. Только видимые (`is_visible`) и не удалённые (`is_deleted`) товары; пустые категории в сайдбаре не показываются.
- **Доступность при заказе** проверяется на нескольких уровнях (карточка → `/api/cart/validate` перед оформлением → `POST /api/orders`). Неактивный товар заказать нельзя.
- **Модалка товара** рендерится клиентом из `OPEN_PRODUCT_DATA`/`/api/product/{slug}` (JS), а не сервером — искать разметку модалки в `static/js/main.js`, не в шаблоне.
- **Админка `/admin/`** защищена: гард `require_admin` на роутере → любой `/admin/*` без сессии отдаёт **404**. Вход через секретный префикс `/admin/login/<ADMIN_URL_SECRET>` + логин/пароль (`AdminUser`, bcrypt). Креды/секрет — в `.env`, учётки засеваются на старте (`seed_admin`): `ADMIN_USERNAME`/`ADMIN_PASSWORD` (роль `admin`) и `OPERATOR_USERNAME`/`OPERATOR_PASSWORD` (роль `operator`). Логика — `app/services/admin_auth.py`, роуты входа — `auth_router`.
- **Роли админки** (`AdminUser.role`): `admin` — полный доступ; `operator` — всё, кроме раздела «Ключи API» (`/admin/api-settings` — API-ключи интеграций), который закрыт `require_role_admin` → оператору **404**, пункт меню скрыт; `seo` — **только** `/admin/seo`, `/admin/products`, `/admin/categories` (белый список `_SEO_ALLOWED_PREFIXES` в `require_admin`), всё остальное, включая дашборд `/admin`, → **404**; после входа редиректится на `/admin/seo`. Роль в шаблонах — переменная `role` (проставляется в `_tmpl` из `request.state.admin`). Общие настройки (`/admin/settings`) доступны `admin` и `operator`.
- **SEO-поля:** у `Product` и `Category` есть `meta_title`/`meta_description`. Пустое значение = метатеги генерируются автоматически (см. фолбэки в `catalog.py`), поэтому в шаблоны эти поля идут уже «схлопнутыми». `robots.txt` переопределяется настройкой `robots_txt` в БД (`/admin/seo`), пустая → захардкоженный дефолт в `main.py`.
- **Кнопки «Сохранить/Отмена»** в полностраничных карточках редактирования (товары, сторис, настройки) вынесены в sticky-`.adm-topbar` и сабмитят форму через атрибут `form="<id>"`. Модальные карточки (промокоды, расписание, зоны) — кнопки внутри `.adm-modal-bg` (`position: fixed`).
- **Стиль кода:** комментарии и сообщения для пользователя — на русском, как в существующем коде. Async везде (`AsyncSession`, `await`). Деньги/время — по правилам выше.

## Проверка изменений

Тесты в проекте отсутствуют — проверяй через работающее приложение: `curl http://localhost:8000/...`, прямые запросы к API, либо разовые скрипты через `docker compose exec web python -c "..."` для чтения/проверки БД. После правок шаблонов/JS обновляй страницу (Ctrl+F5 для статики).
