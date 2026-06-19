"""API и данные для Stories."""
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Story, StorySlide

router = APIRouter(tags=["stories"])


async def get_active_stories(db) -> list[dict]:
    """Загружает активные сторис со слайдами, возвращает список словарей для JSON."""
    now = datetime.utcnow()
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.slides))
        .where(
            Story.is_visible == True,
            (Story.expires_at == None) | (Story.expires_at > now),
        )
        .order_by(Story.sort_order, Story.id)
    )
    stories = result.scalars().all()
    # сторис без слайдов нечего показывать — пропускаем (иначе кружок есть,
    # а просмотрщик открывается и сразу закрывается).
    return [_story_to_dict(s) for s in stories if s.slides]


def _story_to_dict(s: Story) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "cover_image": s.cover_image,
        "slides": [
            {
                "id": sl.id,
                "image_url": sl.image_url,
                "text": sl.text,
                "text_color": sl.text_color,
                "btn_label": sl.btn_label,
                "btn_url": sl.btn_url,
            }
            for sl in s.slides
        ],
    }


@router.get("/stories")
async def api_stories():
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await get_active_stories(db)
