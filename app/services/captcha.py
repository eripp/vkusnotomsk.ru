"""Яндекс SmartCaptcha — серверная валидация токена.

Контракт (yandex.cloud/docs/smartcaptcha):
  POST https://smartcaptcha.cloud.yandex.ru/validate
  form: secret, token, ip → {"status": "ok" | "failed", ...}

Ключи: sitekey (клиентский, отдаётся на фронт) и secret (серверный, для
валидации). Берём из настроек админки (приоритет), иначе из .env. Если secret
не задан — капча считается отключённой (валидация пропускается).
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://smartcaptcha.cloud.yandex.ru/validate"


async def _keys() -> tuple[str, str]:
    """sitekey и secret: настройки админки (приоритет) → .env."""
    sitekey = settings.SMARTCAPTCHA_SITEKEY
    secret = settings.SMARTCAPTCHA_SECRET
    try:
        from app.services.settings import get_site_settings
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            cfg = await get_site_settings(db)
        sitekey = (cfg.get("smartcaptcha_sitekey") or "").strip() or sitekey
        secret = (cfg.get("smartcaptcha_secret") or "").strip() or secret
    except Exception as exc:
        logger.warning("[SmartCaptcha] не удалось прочитать настройки: %s", exc)
    return sitekey, secret


async def get_sitekey() -> str:
    """Клиентский ключ для рендера капчи на фронте (пустой → капча выключена)."""
    sitekey, _ = await _keys()
    return sitekey


async def verify(token: str, ip: str | None = None) -> bool:
    """Проверяет токен SmartCaptcha. Возвращает True, если проверка пройдена
    ИЛИ капча не настроена (secret пустой — валидация пропускается)."""
    _, secret = await _keys()
    if not secret:
        return True  # капча отключена — пропускаем

    if not token:
        logger.info("[SmartCaptcha] пустой токен при включённой капче")
        return False

    data = {"secret": secret, "token": token}
    if ip:
        data["ip"] = ip
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_VALIDATE_URL, data=data)
        result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        # Сетевой сбой валидатора не должен полностью блокировать вход —
        # но и пропускать молча небезопасно. Логируем и считаем непройденной.
        logger.error("[SmartCaptcha] исключение валидации: %s", exc)
        return False

    ok = result.get("status") == "ok"
    if not ok:
        logger.info("[SmartCaptcha] не пройдена: %s", result.get("message") or result)
    return ok
