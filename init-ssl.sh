#!/bin/bash
# Первичное получение SSL-сертификата Let's Encrypt.
# Запускать один раз на сервере ПОСЛЕ того как DNS домена указывает на сервер
# и nginx уже запущен (docker compose up -d nginx).
#
# Usage: bash init-ssl.sh your@email.com vkusnotomsk.ru

set -euo pipefail

EMAIL="${1:?Usage: $0 <email> <domain>}"
DOMAIN="${2:?Usage: $0 <email> <domain>}"

echo "==> Получаем сертификат для ${DOMAIN} ..."
docker compose run --rm \
  certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "${EMAIL}" \
    --agree-tos \
    --no-eff-email \
    -d "${DOMAIN}" \
    -d "www.${DOMAIN}"

echo "==> Перезагружаем nginx ..."
docker compose exec nginx nginx -s reload

echo "==> Готово! Теперь добавьте в crontab авторенью:"
echo "    0 3 * * * cd $(pwd) && docker compose run --rm certbot renew --quiet && docker compose exec nginx nginx -s reload"
