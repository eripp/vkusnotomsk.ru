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
    MAX_API_KEY: str = ""
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SITE_URL: str = "http://localhost"
    OPERATOR_TG_CHAT_ID: str = ""


settings = Settings()
