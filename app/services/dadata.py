"""Dadata: подсказки адресов и обратное геокодирование."""
import httpx
from app.config import settings

SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
GEOLOCATE_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/geolocate/address"
TIMEOUT = 5


def _pack(s: dict) -> dict:
    """Приводим suggestion Dadata к компактному виду для фронта."""
    d = s.get("data") or {}
    return {
        "value": s.get("value"),
        "street": d.get("street_with_type") or d.get("settlement_with_type") or "",
        "house": d.get("house") or "",
        "lat": float(d["geo_lat"]) if d.get("geo_lat") else None,
        "lon": float(d["geo_lon"]) if d.get("geo_lon") else None,
    }


async def suggest_address(query: str, token: str | None = None) -> list[dict]:
    """Прямые подсказки по строке ввода (ограничены Томской областью).

    token — переопределение ключа (из site_settings); иначе берётся из .env.
    """
    key = token or settings.DADATA_TOKEN
    if not key or not query.strip():
        return []
    async with httpx.AsyncClient() as client:
        r = await client.post(
            SUGGEST_URL,
            headers={"Authorization": f"Token {key}"},
            json={"query": query, "count": 7, "locations": [{"region": "Томская"}]},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return [_pack(s) for s in r.json().get("suggestions", [])]


async def geocode_reverse(lat: float, lon: float, token: str | None = None) -> dict | None:
    """Обратное геокодирование: координаты → ближайший адрес."""
    key = token or settings.DADATA_TOKEN
    if not key:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            GEOLOCATE_URL,
            headers={"Authorization": f"Token {key}"},
            json={"lat": lat, "lon": lon, "count": 1, "radius_meters": 200},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        suggestions = r.json().get("suggestions", [])
        return _pack(suggestions[0]) if suggestions else None
