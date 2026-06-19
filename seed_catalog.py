"""Наполнение БД реальным каталогом «Вкусно» (Томск).

Запуск:  docker compose exec web python seed_catalog.py

- Удаляет mock-категории/товары/изображения.
- Создаёт реальные категории и товары.
- Скачивает фото с CDN, конвертирует в webp, кладёт в media/, в БД пишет имя файла.
  Товары без url фото добавляются без изображения (показывается placeholder).
"""
import asyncio
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


# ─── Каталог: категории → список товаров ────────────────────────────────────
# t — название, d — описание, p — цена (руб), w — вес, im — url фото (или None)
CATALOG = [
    ("Блинчики", [
        ("Блинчики с ветчиной и сыром", "Состав: мука пшеничная в/c, ветчина, сыр, майонез, молоко сухое, сахар, яйцо, масло подсолнечное, соль\nСрок годности: при +2+6°C ≤ 72 часов", 162, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KEF7YD6ZGF7F36DPBRVBA.jpg"),
        ("Блинчики с мясом", "Состав: мука пшеничная в/с, говядина, свинина, лук репчатый, молоко сухое, сахар, яйцо, масло подсолнечное, соль, чеснок, перец чёрный молотый\nСрок годности: при +2+6°C ≤ 72 часов", 162, "140 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR5GCCZANT9NNPPDTWNXYBRX.jpg"),
        ("Блинчики «Цезарь»", "Состав: яйцо куриное, мука пшеничная в/с, вода питьевая, сахар-песок, масло, молоко, начинка (филе курицы, томаты, горчица, майонез)\nСрок годности: при +2+6°C ≤ 72 часов", 159, "180 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KE9BMV2QC4KTR0P23T4RA.jpg"),
        ("Блинчики с творогом", "Состав: мука пшеничная в/с, творог, сахар, молоко сухое, масло подсолнечное, яйцо куриное, соль, ванилин\nСрок годности: при +2+6°C ≤ 48 часов", 128, "140 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KE8B47GH0A2KBED1WWPAE.jpg"),
        ("Блинчики с грушей и творожным сыром", "Состав: яйцо куриное, мука пшеничная в/с, молоко сухое. Начинка фруктовая с кусочками фруктов, крем молокосодержащий\nСрок годности: при +2+6°C ≤ 72 часов", 132, "180 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KCCF9Q20G2JBQTBECRPR5.jpg"),
    ]),
    ("Бутерброды", [
        ("Панини с курицей и колбасой", "Состав: мука пшеничная в.с., мясо курицы, огурцы маринованные, вода, колбаса п/к черноспинная, сыр сливочный, кетчуп, маргарин 60%, дрожжи, сахар, аджика, молоко сухое, масло растительное, соль, чеснок, укроп, яйцо\nСрок годности: при +2+6°С ≤ 72 ч", 129, "120 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EKNMCNDTKE2QVRXN42QC8.jpg"),
        ("Панини с ветчиной и помидором", "Состав: мука пшеничная в/с, ветчина, майонез, маргарин, вода питьевая, соль пищевая, маслины, масло подсолнечное, дрожжи, сахар-песок, чесночный порошок, чеснок\nСрок годности: при +2+6°С ≤ 72 ч", 92, "110 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EKM1HRVTWBDY94M07RNXR.jpg"),
        ("Бутерброд №15 с куриной котлетой", "Состав: булочка, курица, майонез, свинина, лук репчатый, кетчуп, сыр, масло подсолнечное, маргарин, сахар, яйцо куриное, дрожжи, чеснок, соль, перец чёрный молотый\nСрок годности: при +2+6°С ≤ 72 часа", 136, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EE75KJP5CXSHHWAPXBWZC.jpg"),
        ("Бутерброд №14 с куриной отбивной", "Состав: булочка, курица, майонез, масло подсолнечное, маргарин, сахар, дрожжи, яйцо куриное, чеснок, соль, специи\nСрок годности: при +2+6°С ≤ 72 часа", 136, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EE593KDVE7ZN49XV24RFZ.jpg"),
        ("Бутерброд №9 с бифштексом", "Состав: булочка, свинина, говядина, майонез, масло подсолнечное, маргарин, сахар, яйцо куриное, дрожжи, чеснок, соль, специи\nСрок годности: при +2+6°С ≤ 72 часа", 125, "190 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EE3C8EC8S1MCJ22QF7PCH.jpg"),
        ("Бутерброд №8 с колбасой полукопчёной", "Состав: булочка, колбаса полукопченая, майонез, огурец консервированный, маргарин, сахар, масло подсолнечное, дрожжи, яйцо куриное, чеснок, соль\nСрок годности: при +2+6°С ≤ 72 часа", 103, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EE1GRHCWK3SRE54XZHZ72.jpg"),
        ("Бутерброд №7 с курицей", "Состав: булочка, курица, майонез, перец болгарский, маргарин, сахар, масло подсолнечное, аджика, дрожжи, яйцо, чеснок, соль\nСрок годности: при +2+6°С ≤ 72 часа", 110, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EDZCC0T23HCT4K0B42NRM.jpg"),
        ("Бутерброд №6 с говядиной", "Состав: булочка, говядина варено-копченая, майонез, кетчуп, сыр, маргарин, сахар, масло подсолнечное, дрожжи, яйцо, чеснок, соль\nСрок годности: при +2+6°С ≤ 72 часа", 136, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EDX9GD16B5DQYJQQZKKQH.jpg"),
        ("Бутерброд №5 с грудинкой", "Состав: булочка, бекон, майонез, огурец консервированный, маргарин, сахар, масло подсолнечное, дрожжи, яйцо, укроп свежий, соль\nСрок годности: при +2+6°С ≤ 72 часа", 121, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EDVJJN5M199XZPY42HPH1.jpg"),
        ("Бутерброд №4 с котлетой", "Состав: булочка, майонез, лук репчатый, кетчуп, свинина, говядина, масло подсолнечное, маргарин, сахар, дрожжи, яйцо куриное, соль, чеснок, перец чёрный молотый\nСрок годности: при +2+6°С ≤ 72 часа", 125, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EDS0JFX9ED1R2XCNGTHEX.jpg"),
        ("Бутерброд №3 хот-дог", "Состав: булочка, сосиска, майонез, кетчуп, сыр, маргарин, сахар, масло подсолнечное, дрожжи, яйцо куриное, чеснок, соль, зелень петрушки\nСрок годности: при +2+6°С ≤ 72 часа", 114, "210 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EDQM3KQTJD22W3Q0ARCBY.jpg"),
        ("Бутерброд №2 с колбасой и сыром", "Состав: булочка, колбаса полукопченая, майонез, сыр, маргарин, сахар, масло подсолнечное, дрожжи, яйцо куриное, чеснок, соль\nСрок годности: при +2+6°С ≤ 72 часа", 110, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KRZWDHSDQJ1BFV60B01VMYC0.jpg"),
        ("Бутерброд №1 с ветчиной и сыром", "Состав: булочка, ветчина, майонез, сыр, маргарин, сахар, масло подсолнечное, дрожжи, яйцо куриное, чеснок, соль\nСрок годности: при +2+6°С ≤ 72 часа", 107, "160 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KRZWC7YESMR2EBHXW3A1F9A9.jpg"),
    ]),
    ("Сладкое", [
        ("Пирожок печёный с чёрной смородиной", "Состав: мука пшеничная, чёрная смородина, сахар, маргарин, загуститель, дрожжи, масло растительное, яичный порошок, соль\nСрок годности: при +2+6°C ≤ 48 часов", 71, "75 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H62JCV54T8JR8GQSNZN6W.jpg"),
        ("Сочень с творогом и изюмом", "Состав: мука пшеничная высшего сорта, маргарин, творог, сахар, курага, яйцо, крахмал, соль, сода, ванилин\nСрок годности: при +2+6°C ≤ 48 часов", 71, "100 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4JEV35VX9XCNQ02FJNZ52R.jpg"),
        ("Пирожок печёный с вишней", "Состав: мука пшеничная, мякоть вишни, сахар, маргарин, загуститель, дрожжи, масло растительное, яичный порошок, соль\nСрок годности: при +2+6°C ≤ 48 часов", 71, "75 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KA5NFV8SSCKM5PQB2WTZW.jpg"),
        ("Пирожок печёный с клубникой", "Состав: мука пшеничная, клубника, сахар, маргарин, загуститель, дрожжи, масло растительное, яичный порошок, соль\nСрок годности: при +2+6°C ≤ 48 часов", 55, "75 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4KAR2VPS9RM6XN451VE5E4.jpg"),
    ]),
    ("Прочие блюда без гарнира", [
        ("Плов", "Состав продукта: говядина, рис, морковь, лук репчатый, масло подсолнечное, соль, перец чёрный\nСрок годности: при +2+6°С ≤ 72 ч", 203, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4FHCZ99ASD1TJR3A3M77D3.jpg"),
        ("Бигус", "Состав продукта: капуста, свинина, морковь, лук репчатый, масло подсолнечное, томатная паста, соль, сахар, перец чёрный, специи\nСрок годности: при +2+6°С ≤ 48 ч", 162, "250 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4EKH9ESQX5GB0BM2N5E9SC.jpg"),
        ("Манты", "Состав: мука пшеничная высшего сорта, говядина, жир животный, лук репчатый, курица, яйцо куриное, соль, специи\nСрок годности: при +2+6°C ≤ 72 часа", 142, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4K4AVK8GF06XCEW9GGA4WF.jpg"),
        ("Голубцы", "Состав: капуста, лук репчатый, свинина, говядина, майонез, рис, масло подсолнечное, соль, перец чёрный молотый, специи\nСрок годности: при +2+6°C ≤ 72 часа", 150, "220 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4HEVXNBY2SDDD5MK4H5CYS.jpg"),
        ("Пельмени отварные", "Состав: свинина, говядина, курица, мука пшеничная, лук репчатый, соль, специи\nСрок годности: при +2+6°C ≤ 72 часа", 142, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4HEYMZJPC3RAJ6KJR7RG2Q.jpg"),
    ]),
    ("Прочие блюда", [
        ("Шашлык куриный жареный во фритюре с макаронами отварными", "Состав основного продукта: мясо куриное, мука пшеничная высшего сорта, масло подсолнечное, соль, перец чёрный\nСостав гарнира: макаронные изделия, масло подсолнечное, масло сливочное, соль\nСрок годности: при +2+6°С ≤ 72 ч", 150, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H4F53QZVNYEDBJSKA82KW.jpg"),
        ("Шашлык куриный жареный во фритюре с картофелем запечённым", "Состав основного продукта: мясо куриное, мука пшеничная высшего сорта, масло подсолнечное, соль, перец чёрный\nСостав гарнира: картофель, майонез, морковь, сыр, масло подсолнечное, соль\nСрок годности: при +2+6°С ≤ 72 ч", 167, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H4D9Q4NASZK0Y7JAD5T3J.jpg"),
        ("Шашлык куриный жареный во фритюре с картофельным пюре", "Состав основного продукта: мясо куриное, мука пшеничная высшего сорта, масло подсолнечное, соль, перец чёрный\nСостав гарнира: картофель, масло сливочное, молоко сухое, соль\nСрок годности: при +2+6°С ≤ 72 ч", 157, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H4BEAH9GV0A9B537D23Q9.jpg"),
        ("Филе куриное гриль с картофелем запечённым", "Состав основного продукта: куриное филе, масло подсолнечное, соус соевый, перец чёрный\nСостав гарнира: картофель, майонез, морковь, сыр, масло подсолнечное, соль\nСрок годности: при +2+6°С ≤ 72 ч", 173, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H49Y1HCQPYA8ZPRQVYVYY.jpg"),
        ("Филе куриное гриль с макаронами отварными", "Состав основного продукта: куриное филе, масло подсолнечное, соус соевый, перец чёрный\nСостав гарнира: макаронные изделия, масло подсолнечное, масло сливочное, соль\nСрок годности: при +2+6°С ≤ 72 ч", 156, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H489K3CBR26DK2X8KCC6V.jpg"),
        ("Филе куриное гриль с рисом отварным и овощами", "Состав основного продукта: куриное филе, масло подсолнечное, соус соевый, перец чёрный\nСостав гарнира: рис, морковь, масло сливочное, масло подсолнечное, перец болгарский, кукуруза консервированная, горошек зелёный консервированный, фасоль стручковая, соль\nСрок годности: при +2+6°С ≤ 72 ч", 156, "200 гр.", "https://platform.foodninja.pro/storage/43c6a0d1-4dbd-403f-b80d-73530b32741b/product/01KR4H45HK34YWYJTBQZ2TPGNZ.jpg"),
        ("Котлета рыбная с отварными макаронами", "Состав основного продукта: филе рыбы, шпик, лук репчатый, масло подсолнечное, хлеб, яйцо куриное, соль, перец чёрный молотый\nСостав гарнира: макаронные изделия, масло подсолнечное, масло сливочное, соль\nСрок годности: при +2+6°С ≤ 72 ч", 140, "225 гр.", None),
        ("Свинина под овощами с картофельным пюре", "Состав основного продукта: свинина, морковь, лук репчатый, мука пшеничная высшего сорта, масло подсолнечное, сыр, яйцо, соль, перец чёрный, чеснок, петрушка\nСостав гарнира: картофель, масло сливочное, молоко сухое, соль\nСрок годности: при +2+6°C ≤ 72 часа", 178, "250 гр.", None),
    ]),
]


async def _save_image_from_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Скачивает фото и сохраняет webp в media/. Возвращает имя файла или None."""
    try:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        img = PILImage.open(io.BytesIO(r.content))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="webp", quality=82, method=4)
        name = f"{uuid.uuid4().hex}.webp"
        with open(f"{MEDIA_DIR}/{name}", "wb") as f:
            f.write(buf.getvalue())
        return name
    except Exception as exc:
        print(f"  ! не удалось скачать {url[:60]}…: {exc}")
        return None


async def main():
    async with AsyncSessionLocal() as db:
        # Чистим mock-данные (включая тестовые заказы, ссылающиеся на товары)
        await db.execute(delete(CashbackTransaction))
        await db.execute(delete(OrderItem))
        await db.execute(delete(Order))
        await db.execute(delete(ProductImage))
        await db.execute(delete(Product))
        await db.execute(delete(Category))
        await db.commit()
        print("Mock-данные удалены (включая тестовые заказы).")

        used_slugs: set[str] = set()

        def uniq(base: str) -> str:
            s = base
            i = 2
            while s in used_slugs:
                s = f"{base}-{i}"
                i += 1
            used_slugs.add(s)
            return s

        async with httpx.AsyncClient(follow_redirects=True) as client:
            for cat_order, (cat_name, items) in enumerate(CATALOG):
                cat = Category(
                    name=cat_name,
                    slug=uniq(slugify(cat_name)),
                    icon=None,
                    sort_order=cat_order,
                    is_visible=True,
                )
                db.add(cat)
                await db.flush()
                print(f"\nКатегория: {cat_name} ({len(items)} товаров)")

                for prod_order, (t, d, price, w, im) in enumerate(items):
                    p = Product(
                        category_id=cat.id,
                        name=t,
                        slug=uniq(slugify(t)),
                        description=d,
                        price=price,
                        weight=w,
                        sort_order=prod_order,
                        is_visible=True,
                        is_deleted=False,
                    )
                    db.add(p)
                    await db.flush()

                    if im:
                        fname = await _save_image_from_url(client, im)
                        if fname:
                            db.add(ProductImage(product_id=p.id, url=fname, sort_order=0))
                    print(f"  + {t} — {price} ₽" + ("" if im else " (без фото)"))

        await db.commit()

        cats = len((await db.execute(select(Category))).scalars().all())
        prods = len((await db.execute(select(Product))).scalars().all())
        imgs = len((await db.execute(select(ProductImage))).scalars().all())
        print(f"\nГотово: категорий {cats}, товаров {prods}, фото {imgs}")


if __name__ == "__main__":
    asyncio.run(main())
