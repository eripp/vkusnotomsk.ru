from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import DeliveryZone

router = APIRouter(tags=["zones"])


@router.get("/zones")
async def get_zones(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DeliveryZone).where(DeliveryZone.is_active == True).order_by(DeliveryZone.priority)
    )
    zones = result.scalars().all()
    return [
        {
            "id": z.id,
            "name": z.name,
            "polygon": z.polygon,
            "color": z.color,
            "delivery_price": z.delivery_price,
            "free_delivery_from": z.free_delivery_from,
            "min_order_sum": z.min_order_sum,
        }
        for z in zones
    ]
