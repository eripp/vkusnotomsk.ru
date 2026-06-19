"""Общий экземпляр Jinja2Templates с глобальными переменными и фильтрами."""
from fastapi.templating import Jinja2Templates
from app.config import settings

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["intcomma"] = lambda v: f"{int(v):,}".replace(",", " ")
templates.env.globals["SITE_URL"] = settings.SITE_URL
