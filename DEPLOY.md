# Развёртывание

Dev и prod работают на одном сервере параллельно, не конфликтуя:

| | dev | prod |
|---|---|---|
| Доступ | http://v2.vkusnotomsk.ru:8000 (напрямую) | https://vkusnotomsk.ru (через nginx) |
| Compose | `docker-compose.yml` (проект по умолчанию) | `docker-compose.prod.yml`, проект `vkusno_prod` |
| Env | `.env` | `.env.prod` |
| БД | том `pgdata` | том `pgdata_prod` (отдельная) |
| Media | `./media` | `./media-prod` |
| web | `--reload` + bind-mount | без reload, без mount кода |
| Cookies | `COOKIE_SECURE=false` | `COOKIE_SECURE=true` |

## Prod: запуск

```bash
# 1. Заполнить секреты в .env.prod (ADMIN_PASSWORD, OPERATOR_PASSWORD,
#    YOOKASSA_*, PLUSOFON_*, SMARTCAPTCHA_*, SMTP_*, DADATA_* и т.д.)
#    SECRET_KEY / ADMIN_URL_SECRET / POSTGRES_PASSWORD уже сгенерированы.

# 2. Поднять стек БЕЗ nginx/certbot (пока нет SSL):
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml up -d --build db web worker

# 3. Применить миграции:
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml exec web alembic upgrade head

# 4. Наполнить каталог (если нужно):
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml exec web python import_products.py

# 5. Выпустить SSL (DNS домена и www ДОЛЖНЫ указывать на этот сервер!).
#    Сначала поднять nginx с временным HTTP-конфигом для ACME, затем certbot.
#    Домен vkusnotomsk.ru → этот сервер (147.45.161.53). www — убедиться, что тоже сюда.
#
#    ВАЖНО: у certbot-сервиса в compose entrypoint = renew-loop. Для разовой
#    команды ОБЯЗАТЕЛЬНО --entrypoint certbot, иначе аргументы игнорируются и
#    контейнер уходит в бесконечный цикл (сертификат не выпустится).
#
#    nginx.conf ссылается на сертификаты, которых ещё нет → сначала поднять nginx
#    с временным HTTP-конфигом (bootstrap) для ACME-challenge, выпустить, вернуть
#    полный конфиг:
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml run --rm --entrypoint certbot certbot certonly \
  --webroot --webroot-path=/var/www/certbot \
  --email ВАШ_EMAIL --agree-tos --no-eff-email \
  -d vkusnotomsk.ru -d www.vkusnotomsk.ru
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate nginx

# 6. Авторенью SSL — уже в crontab (0 3 * * *), команда:
# docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml run --rm --entrypoint certbot certbot renew --quiet && docker compose ... exec nginx nginx -s reload
```

## Обновление прода (деплой новой версии)

```bash
cd /home/jripp/vkusnotomsk_ru
git pull
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml up -d --build web worker
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml exec web alembic upgrade head
```

## Полезное

```bash
# Логи прода
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml logs web --tail 50

# Статус
docker compose -p vkusno_prod --env-file .env.prod -f docker-compose.prod.yml ps
```
