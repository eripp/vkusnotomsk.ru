from datetime import date as Date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import ScheduleEntry
from app.services.schedule_svc import available_days, generate_slots

router = APIRouter(tags=["schedule"])


async def _active_entries(db: AsyncSession) -> list[ScheduleEntry]:
    result = await db.execute(
        select(ScheduleEntry).where(ScheduleEntry.is_active == True)
    )
    return list(result.scalars().all())


@router.get("/available-days")
async def get_available_days(db: AsyncSession = Depends(get_db)):
    entries = await _active_entries(db)
    days = available_days(entries)
    return {
        "days": days,
        # плоский список доступных дат — для совместимости со старым фронтом
        "available": [d["date"] for d in days if d["has_slots"]],
    }


@router.get("/slots")
async def get_slots(date: str, db: AsyncSession = Depends(get_db)):
    try:
        target = Date.fromisoformat(date)
    except ValueError:
        return {"slots": []}
    entries = await _active_entries(db)
    return {"slots": generate_slots(target, entries)}
