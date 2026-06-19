#!/bin/bash
# pg_dump бэкап БД + ротация (хранить 14 дней).
# Добавить в crontab:
#   0 2 * * * bash /path/to/vkusnotomsk_ru/backup.sh >> /var/log/vkusno-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/vkusno}"
KEEP_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILE="${BACKUP_DIR}/vkusno_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[$(date)] Начало бэкапа → ${FILE}"

# Запускаем pg_dump внутри контейнера db
docker compose -f "$(dirname "$0")/docker-compose.yml" exec -T db \
  pg_dump -U vkusno vkusno | gzip > "${FILE}"

echo "[$(date)] Готово. Размер: $(du -sh "${FILE}" | cut -f1)"

# Удаляем бэкапы старше KEEP_DAYS дней
find "${BACKUP_DIR}" -name "vkusno_*.sql.gz" -mtime +${KEEP_DAYS} -delete
echo "[$(date)] Ротация завершена. Бэкапов в наличии: $(ls "${BACKUP_DIR}"/vkusno_*.sql.gz 2>/dev/null | wc -l)"
