"""Импорт каталога из product.csv в БД.

CSV (TSV, разделитель — табуляция) экспортирован с другого сайта. Колонки:
  Категория, Название, Цена, Артикул, Описание (HTML), Ссылка на изображение,
  Вес гр., Кол-во штук в ролле/сете, Кол-во персон, Метки, У товара есть вариации

Что делает скрипт:
  - Удаляет текущий каталог (категории/товары/фото) и тестовые заказы,
    ссылающиеся на товары (как seed_catalog.py). Режим «заменить весь каталог».
  - Создаёт категории в порядке их первого появления в CSV.
  - Парсит HTML-описание: «Состав…» → composition, «Срок годности…» →
    shelf_life, остальное → description.
  - Скачивает фото со стороннего CDN, конвертирует в webp, кладёт в media/,
    в БД пишет ТОЛЬКО имя локального файла (никаких внешних ссылок).

Запуск:  docker compose exec web python import_products.py
"""
import asyncio
import csv
import html
import io
import re
import unicodedata
import uuid

import httpx
from PIL import Image as PILImage
from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models import (
    CashbackTransaction, Category, Order, OrderItem, Product, ProductImage,
)

CSV_PATH = "product.csv"
MEDIA_DIR = "media"

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "«": "", "»": "",
}


def slugify(text: str) -> str:
    text = text.lower().strip()
    out = "".join(TRANSLIT.get(ch, ch) for ch in text)
    out = unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out or "item"


def _strip_html(s: str) -> list[str]:
    """HTML → список непустых строк (теги <p>/<br> → переносы)."""
    s = re.sub(r"(?i)</p\s*>", "\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return [ln for ln in lines if ln]


def parse_description(raw_html: str) -> tuple[str | None, str | None, str | None]:
    """Разбивает описание на (composition, shelf_life, description)."""
    composition: list[str] = []
    shelf_life: str | None = None
    other: list[str] = []
    for ln in _strip_html(raw_html or ""):
        low = ln.lower()
        if low.startswith("срок годности"):
            shelf_life = ln.split(":", 1)[1].strip() if ":" in ln else ln
        elif low.startswith("состав"):
            composition.append(ln)
        else:
            other.append(ln)
    return (
        "\n".join(composition) or None,
        shelf_life,
        "\n".join(other) or None,
    )


def parse_price(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else 0


def parse_weight(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return f"{raw} г" if raw.isdigit() else raw


async def _save_image_from_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Скачивает фото и сохраняет webp в media/. Возвращает имя файла или None."""
    try:
        r = await client.get(url, timeout=30)
        r.raise_for_status()
        img = PILImage.open(io.BytesIO(r.content))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        # ресайз по длинной стороне до 1920px (как в админке)
        w, h = img.size
        if max(w, h) > 1920:
            ratio = 1920 / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="webp", quality=82, method=4)
        name = f"{uuid.uuid4().hex}.webp"
        with open(f"{MEDIA_DIR}/{name}", "wb") as f:
            f.write(buf.getvalue())
        return name
    except Exception as exc:
        print(f"  ! не удалось скачать {url[:60]}…: {exc}")
        return None


def read_rows() -> list[dict]:
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


async def main():
    rows = read_rows()
    print(f"Прочитано строк из {CSV_PATH}: {len(rows)}")

    async with AsyncSessionLocal() as db:
        # Режим «заменить весь каталог»: чистим текущие данные.
        await db.execute(delete(CashbackTransaction))
        await db.execute(delete(OrderItem))
        await db.execute(delete(Order))
        await db.execute(delete(ProductImage))
        await db.execute(delete(Product))
        await db.execute(delete(Category))
        await db.commit()
        print("Текущий каталог и тестовые заказы удалены.")

        used_slugs: set[str] = set()

        def uniq(base: str) -> str:
            s, i = base, 2
            while s in used_slugs:
                s = f"{base}-{i}"
                i += 1
            used_slugs.add(s)
            return s

        # категории — в порядке первого появления
        categories: dict[str, Category] = {}
        cat_order = 0

        ok_img = no_img = fail_img = 0

        async with httpx.AsyncClient(follow_redirects=True) as client:
            for prod_order, row in enumerate(rows):
                cat_name = (row.get("Категория") or "Без категории").strip()
                name = (row.get("Название") or "").strip()
                if not name:
                    continue

                cat = categories.get(cat_name)
                if cat is None:
                    cat = Category(
                        name=cat_name,
                        slug=uniq(slugify(cat_name)),
                        icon=None,
                        sort_order=cat_order,
                        is_visible=True,
                    )
                    db.add(cat)
                    await db.flush()
                    categories[cat_name] = cat
                    cat_order += 1
                    print(f"\nКатегория: {cat_name}")

                composition, shelf_life, description = parse_description(row.get("Описание", ""))

                p = Product(
                    category_id=cat.id,
                    name=name,
                    slug=uniq(slugify(name)),
                    description=description,
                    composition=composition,
                    shelf_life=shelf_life,
                    price=parse_price(row.get("Цена", "")),
                    weight=parse_weight(row.get("Вес, гр.", "")),
                    sort_order=prod_order,
                    is_visible=True,
                    is_deleted=False,
                )
                db.add(p)
                await db.flush()

                url = (row.get("Ссылка на изображение") or "").strip()
                tag = ""
                if url:
                    fname = await _save_image_from_url(client, url)
                    if fname:
                        db.add(ProductImage(product_id=p.id, url=fname, sort_order=0))
                        ok_img += 1
                    else:
                        fail_img += 1
                        tag = " (фото не скачалось)"
                else:
                    no_img += 1
                    tag = " (без фото)"

                print(f"  + {name} — {p.price} ₽{tag}")

        await db.commit()

        cats = len((await db.execute(select(Category))).scalars().all())
        prods = len((await db.execute(select(Product))).scalars().all())
        imgs = len((await db.execute(select(ProductImage))).scalars().all())
        print(
            f"\nГотово: категорий {cats}, товаров {prods}, фото {imgs} "
            f"(скачано {ok_img}, без url {no_img}, ошибок {fail_img})"
        )


if __name__ == "__main__":
    asyncio.run(main())
