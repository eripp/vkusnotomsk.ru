from shapely.geometry import Point, shape
from app.models import DeliveryZone


def find_zone(lat: float, lon: float, zones: list[DeliveryZone]) -> DeliveryZone | None:
    pt = Point(lon, lat)  # shapely: (x=lon, y=lat)
    best: DeliveryZone | None = None
    for z in sorted(zones, key=lambda z: z.priority, reverse=True):
        if not z.is_active:
            continue
        try:
            poly = shape(z.polygon)
            if poly.contains(pt):
                if best is None or z.priority > best.priority:
                    best = z
        except Exception:
            continue
    return best
