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


from markupsafe import Markup

# ─── Контурные SVG-иконки категорий (stroke = currentColor) ──────────────────
_ICON_PATHS = {
    # тарелка с приборами (дефолт / горячее)
    "plate": '<circle cx="12" cy="13" r="7"/><path d="M3 4v5M3 4c-1.2 0-1.2 5 0 5M21 4v16M21 4c1 0 1 4 0 5"/>',
    "meat":  '<path d="M14 8a5 5 0 0 0-8 5l-2.5 2.5a2 2 0 0 0 2.8 2.8L8.9 16A5 5 0 0 0 14 8z"/><circle cx="16.5" cy="7.5" r="2.5"/>',
    "sandwich": '<rect x="3" y="9" width="18" height="5" rx="2"/><path d="M4 9c2-3 14-3 16 0M4 14c1.5 2.5 14.5 2.5 16 0"/>',
    "soup":  '<path d="M4 10h16a8 8 0 0 1-16 0z"/><path d="M9 6c0-1 1-1 1-2M13 6c0-1 1-1 1-2M3 20h18"/>',
    "porridge": '<path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "salad": '<path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M9 11c-1-3 1-5 3-5s4 2 3 5M12 6V4"/>',
    "bakery": '<path d="M6 8c0-2 2-3 6-3s6 1 6 3-1 11-2 12H8C7 19 6 10 6 8z"/><path d="M10 8v10M14 8v10"/>',
    "sweet": '<path d="M5 10h14l-1.2 9H6.2L5 10z"/><path d="M8 10c0-3 1.8-5 4-5s4 2 4 5"/><path d="M12 3v2"/>',
    "pancake": '<ellipse cx="12" cy="9" rx="8" ry="3"/><path d="M4 9v3c0 1.7 3.6 3 8 3s8-1.3 8-3V9"/>',
    "snack": '<path d="M5 7l9-2 5 5-2 9-9 2-5-5z"/><circle cx="11" cy="11" r="2"/>',
}
_ICON_RULES = [
    ("salat", "salad"), ("салат", "salad"),
    ("sup", "soup"), ("суп", "soup"),
    ("kash", "porridge"), ("каш", "porridge"), ("молочн", "porridge"),
    ("buter", "sandwich"), ("бутер", "sandwich"), ("сэндвич", "sandwich"), ("sendvich", "sandwich"), ("панини", "sandwich"),
    ("vypech", "bakery"), ("выпеч", "bakery"), ("пирож", "bakery"), ("булоч", "bakery"),
    ("slad", "sweet"), ("слад", "sweet"), ("десерт", "sweet"),
    ("blin", "pancake"), ("блин", "pancake"),
    ("zakus", "snack"), ("закус", "snack"), ("новинк", "snack"), ("шаурма", "snack"),
    ("garnir", "plate"), ("гарнир", "plate"), ("втор", "meat"), ("vtor", "meat"),
]

def _cat_icon(name: str, slug: str = "", stored: str = "") -> Markup:
    """Иконка категории:
      • stored = путь к файлу (/media/.. или *.svg/png) → <img>;
      • stored = эмодзи/короткий текст → как есть;
      • иначе → контурный SVG по названию (автоподбор).
    """
    from markupsafe import escape
    s = (stored or "").strip()
    if s:
        is_file = s.startswith(("/media/", "/static/", "http")) or s.lower().endswith((".svg", ".png", ".jpg", ".jpeg", ".webp"))
        if is_file:
            return Markup(f'<img class="cat-img" src="{escape(s)}" alt="">')
        # короткая строка — считаем эмодзи
        if len(s) <= 4:
            return Markup(f'<span class="cat-emoji">{escape(s)}</span>')
    key = f"{name or ''} {slug or ''}".lower()
    icon = "plate"
    for sub, ic in _ICON_RULES:
        if sub in key:
            icon = ic
            break
    paths = _ICON_PATHS[icon]
    return Markup(
        f'<svg class="cat-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["intcomma"] = lambda v: f"{int(v):,}".replace(",", " ")
templates.env.globals["SITE_URL"] = settings.SITE_URL
# Название бренда — вызывать в шаблонах как site_name(request)
templates.env.globals["site_name"] = _site_name
# Контурная SVG-иконка категории по названию: cat_icon(name, slug)
templates.env.globals["cat_icon"] = _cat_icon
# «1 вариант / 2 варианта / 5 вариантов»
templates.env.filters["variants_word"] = lambda n: _plural_ru(n, "вариант", "варианта", "вариантов")
