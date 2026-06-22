"""Общий экземпляр Jinja2Templates с глобальными переменными и фильтрами."""
from fastapi.templating import Jinja2Templates
from app.config import settings

def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


DEFAULT_SITE_NAME = "Фабрика-кухня Вкусно"


def _site_name(request) -> str:
    """Название бренда из настроек (site_name), иначе дефолт."""
    ss = getattr(getattr(request, "state", None), "site_settings", None) or {}
    return (ss.get("site_name") or "").strip() or DEFAULT_SITE_NAME


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["intcomma"] = lambda v: f"{int(v):,}".replace(",", " ")
templates.env.globals["SITE_URL"] = settings.SITE_URL
# Название бренда — вызывать в шаблонах как site_name(request)
templates.env.globals["site_name"] = _site_name
# «1 вариант / 2 варианта / 5 вариантов»
templates.env.filters["variants_word"] = lambda n: _plural_ru(n, "вариант", "варианта", "вариантов")
