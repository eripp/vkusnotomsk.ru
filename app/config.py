from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://vkusno:password@db:5432/vkusno"
    SECRET_KEY: str = "dev-secret"
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET: str = ""
    DADATA_TOKEN: str = ""
    DADATA_SECRET: str = ""
    TG_BOT_TOKEN: str = ""
    TG_BOT_USERNAME: str = ""
    TG_GATEWAY_TOKEN: str = ""   # Telegram Gateway API (gatewayapi.telegram.org) — коды в Telegram по номеру
    MAX_API_KEY: str = ""
    SMSRU_API_ID: str = ""       # SMS.RU — api_id для отправки кодов по SMS
    IDGTL_API_KEY: str = ""      # i-dgtl — ключ для Authorization: Basic (модуль верификации)
    IDGTL_GATEWAY_ID: str = ""   # i-dgtl — ID модуля подтверждения (gatewayId)
    SMARTCAPTCHA_SITEKEY: str = ""   # Яндекс SmartCaptcha — клиентский ключ (на фронте)
    SMARTCAPTCHA_SECRET: str = ""    # Яндекс SmartCaptcha — серверный ключ (валидация токена)
    PLUSOFON_CLIENT_ID: str = ""     # Plusofon — Client (id клиента)
    PLUSOFON_TOKEN: str = ""         # Plusofon — Bearer token
    PLUSOFON_NUMBER_ID: str = ""     # Plusofon — id номера-отправителя
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SITE_URL: str = "http://localhost"
    OPERATOR_TG_CHAT_ID: str = ""
    # Админка: секретный префикс для входа + первичные логин/пароль (засеваются в БД)
    ADMIN_URL_SECRET: str = ""
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    OPERATOR_USERNAME: str = "operator"
    OPERATOR_PASSWORD: str = ""


settings = Settings()
