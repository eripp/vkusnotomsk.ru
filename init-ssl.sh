#!/bin/bash
# Первичное получение SSL-сертификата Let's Encrypt.
# Запускать на сервере ПОСЛЕ того как DNS домена указывает на сервер
# и nginx уже запущен (docker compose up -d nginx).
#
# Usage:
#   bash init-ssl.sh your@email.com vkusnotomsk.ru             # домен + www
#   bash init-ssl.sh your@email.com v2.vkusnotomsk.ru --no-www # только сам домен

set -euo pipefail

EMAIL="${1:?Usage: $0 <email> <domain> [--no-www]}"
DOMAIN="${2:?Usage: $0 <email> <domain> [--no-www]}"
NOWWW="${3:-}"

DOMAIN_ARGS=(-d "${DOMAIN}")
if [ "${NOWWW}" != "--no-www" ]; then
  DOMAIN_ARGS+=(-d "www.${DOMAIN}")
fi

echo "==> Получаем сертификат для ${DOMAIN} (${DOMAIN_ARGS[*]}) ..."
docker compose run --rm \
  certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "${EMAIL}" \
    --agree-tos \
    --no-eff-email \
    "${DOMAIN_ARGS[@]}"

echo "==> Перезагружаем nginx ..."
docker compose exec nginx nginx -s reload

echo "==> Готово! Сертификат для ${DOMAIN} выпущен."
echo "    Авторенью (один раз в crontab, общий для всех доменов):"
echo "    0 3 * * * cd $(pwd) && docker compose run --rm certbot renew --quiet && docker compose exec nginx nginx -s reload"
