// ─── Каталог: один длинный список с секциями по категориям ───────────────────
//  • клик по категории в баре → плавный скролл к якорю секции
//  • ручной скролл → в баре подсвечивается текущая категория (scroll-spy)
//  • поиск (debounce) фильтрует уже загруженные товары без перезагрузки

const HEADER_OFFSET = 64;   // высота фиксированной шапки + небольшой зазор

// ── Навигация по категориям (якоря) ──────────────────────────────────────────
const catItems = [...document.querySelectorAll('.cat-item')];
const catSections = [...document.querySelectorAll('.cat-section')];

function setActiveCat(slug) {
  catItems.forEach(i => i.classList.toggle('active', i.dataset.slug === slug));
}

function scrollToCat(slug) {
  const sec = document.getElementById(`cat-${slug}`);
  if (!sec) return;
  const y = sec.getBoundingClientRect().top + window.scrollY - HEADER_OFFSET;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

catItems.forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    const slug = item.dataset.slug;
    setActiveCat(slug);
    scrollToCat(slug);
    history.replaceState(null, '', slug === 'popular' ? '/' : `/category/${slug}`);
  });
});

// ── Scroll-spy: подсветка текущей категории при ручной прокрутке ──────────────
let spyTicking = false;
function updateScrollSpy() {
  spyTicking = false;
  if (document.getElementById('search-results').style.display !== 'none') return;
  const probe = window.scrollY + HEADER_OFFSET + 8;
  let current = catSections[0];
  for (const sec of catSections) {
    if (sec.offsetTop <= probe) current = sec;
    else break;
  }
  if (current) setActiveCat(current.dataset.slug);
}
window.addEventListener('scroll', () => {
  if (!spyTicking) { spyTicking = true; requestAnimationFrame(updateScrollSpy); }
}, { passive: true });

// ── При прямом заходе на /category/slug — скроллим к секции ───────────────────
window.addEventListener('DOMContentLoaded', () => {
  const initial = (typeof INITIAL_CATEGORY !== 'undefined' && INITIAL_CATEGORY) || '';
  if (initial && document.getElementById(`cat-${initial}`)) {
    setActiveCat(initial);
    // без анимации при загрузке
    const sec = document.getElementById(`cat-${initial}`);
    window.scrollTo({ top: sec.offsetTop - HEADER_OFFSET });
  } else {
    updateScrollSpy();
  }
});

// ─── Поиск с debounce (фильтр по загруженным товарам) ────────────────────────
const searchInput = document.getElementById('search-input');
let searchTimer = null;

if (searchInput) {
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(searchInput.value.trim()), 300);
  });
}

async function runSearch(q) {
  const searchSec = document.getElementById('search-results');
  const empty     = document.getElementById('search-empty');

  if (!q) {
    // выходим из режима поиска — показываем секции категорий
    searchSec.style.display = 'none';
    empty.classList.add('hidden');
    catSections.forEach(s => s.style.display = '');
    updateScrollSpy();
    return;
  }

  // в режиме поиска прячем категорийные секции
  catSections.forEach(s => s.style.display = 'none');

  const res  = await fetch(`/api/products?search=${encodeURIComponent(q)}`);
  const data = await res.json();
  const grid = document.getElementById('grid-search');

  if (data.products.length) {
    grid.innerHTML = data.products.map(cardHTML).join('');
    searchSec.style.display = '';
    empty.classList.add('hidden');
  } else {
    grid.innerHTML = '';
    searchSec.style.display = 'none';
    empty.classList.remove('hidden');
  }
  window.scrollTo({ top: 0 });
  cart._render();
}

function cardHTML(p) {
  const labels = [
    p.label_popular ? '<span class="label label-popular">⭐</span>' : '',
    p.label_halal   ? '<span class="label label-halal">☪</span>'   : '',
    p.label_post    ? '<span class="label label-post">✦</span>'    : '',
    p.label_new     ? '<span class="label label-new">New</span>'   : '',
    p.label_kids    ? '<span class="label label-kids">👧</span>'   : '',
    p.label_vegan   ? '<span class="label label-vegan">🌿</span>'  : '',
  ].join('');

  const img = p.image
    ? `<img class="product-img" src="${p.image}" alt="${esc(p.name)}" loading="lazy">`
    : `<div class="product-img product-img-placeholder">🍱</div>`;

  return `<article class="product-card" data-slug="${p.slug}" onclick="productModal.open('${p.slug}')">
    <div class="product-img-wrap">
      ${img}
      <div class="product-labels">${labels}</div>
    </div>
    <div class="product-info">
      <div class="product-name">${esc(p.name)}</div>
      ${p.variants_count > 1 ? `<div class="product-variants-hint">ещё ${p.variants_count - 1} ${variantsWord(p.variants_count - 1)}</div>` : ''}
      ${p.weight ? `<div class="product-weight">${esc(p.weight)}</div>` : ''}
      ${(p.kcal || p.protein || p.fat || p.carbs) ? `<div class="product-nutrition">
        ${p.kcal ? `<span class="pn-kcal">${p.kcal} ккал</span>` : ''}
        <span class="pn-macros">Б ${p.protein || 0} · Ж ${p.fat || 0} · У ${p.carbs || 0}</span>
      </div>` : ''}
      <div class="product-footer">
        <span class="product-price">${p.price} ₽</span>
        <button class="add-btn" data-id="${p.id}"
          data-name="${esc(p.name)}" data-price="${p.price}"
          data-image="${p.image||''}" data-weight="${esc(p.weight||'')}"
          onclick="event.stopPropagation(); cart.addFromBtn(this)">
          <span id="add-btn-${p.id}">+</span>
        </button>
      </div>
    </div>
  </article>`;
}

// ─── Модал товара ─────────────────────────────────────────────────────────────
const productModal = {
  _current: null,   // текущий slug

  async open(slug) {
    this._current = slug;
    history.pushState({ product: slug }, '', `/product/${slug}`);
    document.getElementById('modal-overlay').classList.add('open');
    const modal = document.getElementById('product-modal');
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    document.getElementById('modal-content').innerHTML =
      '<div style="padding:60px;text-align:center;color:#aaa;font-size:24px">⏳</div>';

    try {
      const res = await fetch(`/api/product/${slug}`);
      if (!res.ok) throw new Error('not found');
      const p = await res.json();
      document.getElementById('modal-content').innerHTML = this._html(p);
      this._initGallery(p.images || []);
      _pmUpdateCounter(p.id);
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ ecommerce: { detail: { products: [{ id: p.id, name: p.name, price: p.price }] } } });
    } catch {
      document.getElementById('modal-content').innerHTML =
        '<div style="padding:40px;text-align:center;color:#aaa">Товар не найден</div>';
    }
  },

  close() {
    _closeModal();
    history.back();
  },

  _html(p) {
    // галерея
    const images = p.images && p.images.length ? p.images : [];
    let gallery = '';
    if (images.length) {
      gallery = `<div class="pm-gallery">
        <img class="pm-gallery-main" id="pm-main-img" src="${images[0]}" alt="${esc(p.name)}">
        ${images.length > 1 ? `<div class="pm-dots">${images.map((_, i) =>
          `<button class="pm-dot ${i===0?'active':''}" data-idx="${i}" onclick="productModal._goSlide(${i})"></button>`
        ).join('')}</div>` : ''}
      </div>`;
    } else {
      gallery = `<div class="pm-gallery"><div class="pm-gallery-main-placeholder">🍱</div></div>`;
    }

    // метки
    const labelsHtml = [
      p.label_popular ? '<span class="pm-label pm-label-popular">⭐ Часто заказывают</span>' : '',
      p.label_halal   ? '<span class="pm-label pm-label-halal">☪ Халяль</span>'   : '',
      p.label_post    ? '<span class="pm-label pm-label-post">✦ Пост</span>'    : '',
      p.label_new     ? '<span class="pm-label pm-label-new">🆕 Новинка</span>'   : '',
      p.label_kids    ? '<span class="pm-label pm-label-kids">👧 Детям</span>'   : '',
      p.label_vegan   ? '<span class="pm-label pm-label-vegan">🌿 Vegan</span>'  : '',
    ].filter(Boolean).join('');

    // варианты
    let variantsHtml = '';
    if (p.variants && p.variants.length > 1) {
      variantsHtml = `<div class="pm-variants">` +
        p.variants.map(v =>
          `<button class="pm-variant-btn ${v.slug === p.slug ? 'active' : ''}"
            onclick="productModal.open('${v.slug}')">${esc(v.label)}</button>`
        ).join('') +
        `</div>`;
    }

    // КБЖУ
    let nutritionHtml = '';
    if (p.kcal || p.protein || p.fat || p.carbs) {
      nutritionHtml = `<div class="pm-section-title">На порцию</div>
        <div class="pm-nutrition">
          <div class="pm-nut-item"><div class="pm-nut-val">${p.kcal ?? '—'}</div><div class="pm-nut-label">Ккал</div></div>
          <div class="pm-nut-item"><div class="pm-nut-val">${p.protein ?? '—'}</div><div class="pm-nut-label">Белки</div></div>
          <div class="pm-nut-item"><div class="pm-nut-val">${p.fat ?? '—'}</div><div class="pm-nut-label">Жиры</div></div>
          <div class="pm-nut-item"><div class="pm-nut-val">${p.carbs ?? '—'}</div><div class="pm-nut-label">Углеводы</div></div>
        </div>`;
    }

    // состав
    let compositionHtml = '';
    const composition = cleanText(p.composition);
    if (composition) {
      compositionHtml = `<div class="pm-section-title">Состав</div>
        <div class="pm-text collapsed" id="pm-composition">${esc(composition)}</div>
        <button class="pm-expand" onclick="pmExpand('pm-composition', this)">Показать полностью ↓</button>`;
    }

    // описание
    let descHtml = '';
    const description = cleanText(p.description);
    if (description) {
      descHtml = `<div class="pm-section-title">Описание</div>
        <div class="pm-text">${esc(description)}</div>`;
    }

    // доп. инфо
    let metaHtml = '';
    const metaRows = [
      p.shelf_life   ? `<div class="pm-meta-row"><strong>Срок хранения:</strong> ${esc(p.shelf_life)}</div>`   : '',
      p.storage_cond ? `<div class="pm-meta-row"><strong>Условия хранения:</strong> ${esc(p.storage_cond)}</div>` : '',
    ].filter(Boolean);
    if (metaRows.length) metaHtml = `<div class="pm-meta">${metaRows.join('')}</div>`;

    // рекомендации
    let recsHtml = '';
    if (p.recommendations && p.recommendations.length) {
      const recCards = p.recommendations.map(r => `
        <div class="pm-rec-card" onclick="productModal.open('${r.slug}')">
          ${r.image
            ? `<img class="pm-rec-img" src="${r.image}" alt="${esc(r.name)}" loading="lazy">`
            : `<div class="pm-rec-img-ph">🍱</div>`}
          <div class="pm-rec-info">
            <div class="pm-rec-name">${esc(r.name)}</div>
            <div class="pm-rec-price">${r.price} ₽</div>
          </div>
        </div>`).join('');
      recsHtml = `<div class="pm-recs-title">Что ещё пригодится</div>
        <div class="pm-recs-scroll">${recCards}</div>`;
    }

    return `
      ${gallery}
      <div class="pm-detail">
        <div class="pm-body">
          <h2 class="pm-title">${esc(p.name)}</h2>
          ${p.weight ? `<div class="pm-weight">${esc(p.weight)}</div>` : ''}
          ${labelsHtml ? `<div class="pm-labels">${labelsHtml}</div>` : ''}
          ${variantsHtml}
          ${nutritionHtml}
          ${descHtml}
          ${compositionHtml}
          ${metaHtml}
          ${recsHtml}
        </div>
        <div class="pm-footer">
          <span class="pm-price" id="pm-price">${p.price} ₽</span>
          ${p.available === false ? `
          <button class="pm-add-btn" disabled
            style="background:#ccc;cursor:not-allowed">Товар отсутствует</button>
          ` : `
          <div class="add-btn-counter pm-counter" id="pm-counter-${p.id}" style="display:none">
            <button onclick="cart.dec(${p.id}); _pmUpdateCounter(${p.id})">−</button>
            <span class="qty" id="pm-qty-${p.id}">0</span>
            <button data-id="${p.id}" data-name="${esc(p.name)}" data-price="${p.price}"
              data-image="${p.image||''}" data-weight="${esc(p.weight||'')}"
              onclick="cart.addFromBtn(this); _pmUpdateCounter(${p.id})">+</button>
          </div>
          <button class="pm-add-btn" id="pm-add-${p.id}"
            data-id="${p.id}" data-name="${esc(p.name)}" data-price="${p.price}"
            data-image="${p.image||''}" data-weight="${esc(p.weight||'')}"
            onclick="cart.addFromBtn(this); _pmUpdateCounter(${p.id})">
            + В корзину
          </button>
          `}
        </div>
      </div>`;
  },

  _goSlide(idx) {
    const main = document.getElementById('pm-main-img');
    if (main) main.src = main.dataset[`src${idx}`] || main.src;
    // обновляем через сохранённый список
    if (this._images && this._images[idx]) {
      main.src = this._images[idx];
    }
    document.querySelectorAll('.pm-dot').forEach((d, i) => d.classList.toggle('active', i === idx));
  },

  _initGallery(images) {
    this._images = images;
    // точки уже созданы в HTML, просто сохраняем список
  },
};

function _closeModal() {
  document.getElementById('modal-overlay')?.classList.remove('open');
  document.getElementById('product-modal')?.classList.remove('open');
  document.body.style.overflow = '';
  productModal._current = null;
}

function _pmUpdateCounter(id) {
  const qty = cart.qty(id);
  const addBtn  = document.getElementById(`pm-add-${id}`);
  const counter = document.getElementById(`pm-counter-${id}`);
  const qtyEl   = document.getElementById(`pm-qty-${id}`);
  if (!addBtn || !counter) return;
  if (qty > 0) {
    addBtn.style.display = 'none';
    counter.style.display = '';
    if (qtyEl) qtyEl.textContent = qty;
  } else {
    addBtn.style.display = '';
    counter.style.display = 'none';
  }
}

function pmExpand(id, btn) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('collapsed');
  btn.style.display = 'none';
}

// ─── dataLayer: begin_checkout при клике на "Оформить заказ" ─────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('click', e => {
    if (e.target.closest('a[href="/checkout"]')) {
      const items = cart.items;
      if (!items.length) return;
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({
        ecommerce: {
          checkout: {
            actionField: { step: 1 },
            products: items.map(i => ({ id: i.id, name: i.name, price: i.price, quantity: i.qty })),
          },
        },
      });
    }
  });
});

// ─── Автооткрытие при прямом переходе на /product/slug ───────────────────────
document.addEventListener('DOMContentLoaded', () => {
  if (typeof OPEN_PRODUCT !== 'undefined' && OPEN_PRODUCT) {
    // данные уже есть на странице — рендерим сразу без fetch
    if (typeof OPEN_PRODUCT_DATA !== 'undefined' && OPEN_PRODUCT_DATA) {
      document.getElementById('modal-overlay').classList.add('open');
      document.getElementById('product-modal').classList.add('open');
      document.body.style.overflow = 'hidden';
      document.getElementById('modal-content').innerHTML = productModal._html(OPEN_PRODUCT_DATA);
      productModal._initGallery(OPEN_PRODUCT_DATA.images || []);
      _pmUpdateCounter(OPEN_PRODUCT_DATA.id);
    } else {
      productModal.open(OPEN_PRODUCT);
    }
  }
});

// ─── Кнопка «назад» закрывает открытый модал товара ──────────────────────────
window.addEventListener('popstate', () => {
  const modal = document.getElementById('product-modal');
  if (modal && modal.classList.contains('open')) _closeModal();
});

// ─── Горизонтальный скролл «Что ещё пригодится» колесом мыши ─────────────────
document.addEventListener('wheel', (e) => {
  const strip = e.target.closest && e.target.closest('.pm-recs-scroll');
  if (!strip) return;
  // вертикальный жест колеса → горизонтальная прокрутка ленты
  const delta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
  if (!delta) return;
  // прокручиваем, пока есть куда (иначе не блокируем обычный скролл страницы)
  const atStart = strip.scrollLeft <= 0;
  const atEnd = strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 1;
  if ((delta < 0 && atStart) || (delta > 0 && atEnd)) return;
  strip.scrollLeft += delta;
  e.preventDefault();
}, { passive: false });

// ─── Авторизация: если вошли — показываем имя в кнопке «Личный кабинет» ──────
(async () => {
  try {
    const res = await fetch('/api/auth/me');
    if (res.ok) {
      const user = await res.json();
      localStorage.setItem('vkusno_user', JSON.stringify(user));
      const lbl = document.getElementById('account-btn-label');
      if (lbl) lbl.textContent = user.name || user.phone;
    } else {
      localStorage.removeItem('vkusno_user');
    }
  } catch { /* не критично */ }
})();

// ─── Утилиты ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// «1 вариант / 2 варианта / 5 вариантов»
function variantsWord(n) {
  n = Math.abs(n);
  if (n % 10 === 1 && n % 100 !== 11) return 'вариант';
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'варианта';
  return 'вариантов';
}

// Текстовое поле к показу: null/none/null-строки → пусто (защита от мусорных данных)
function cleanText(s) {
  if (s == null) return '';
  const t = String(s).trim();
  return (t.toLowerCase() === 'none' || t.toLowerCase() === 'null') ? '' : t;
}

// Нормализует ссылку кнопки слайда: внутренний путь (/...) оставляем как есть,
// внешний адрес без схемы (ozon.ru) → https://, иначе браузер считает его
// относительным путём сайта.
function normalizeUrl(url) {
  const u = String(url).trim();
  if (!u) return '#';
  if (u.startsWith('/') || u.startsWith('#')) return u;     // внутренняя ссылка/якорь
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(u)) return u;         // уже есть схема (http://, https://, …)
  if (/^(mailto:|tel:)/i.test(u)) return u;
  return 'https://' + u;                                    // голый домен → внешняя ссылка
}

// ─── Story Viewer ──────────────────────────────────────────────────────────────
const storyViewer = {
  _stories:    [],   // [{id, title, cover_image, slides:[{image_url,text,text_color,btn_label,btn_url}]}]
  _storyIdx:   0,    // текущая история
  _slideIdx:   0,    // текущий слайд
  _timer:      null,
  _DURATION:   5000, // мс на слайд
  _seen:       new Set(JSON.parse(localStorage.getItem('vkusno_seen_stories') || '[]')),

  init() {
    if (typeof STORIES_DATA === 'undefined' || !STORIES_DATA.length) return;
    this._stories = STORIES_DATA;
    // отмечаем просмотренные визуально
    this._stories.forEach((s, i) => {
      if (this._seen.has(s.id)) {
        const ring = document.getElementById(`story-ring-${i}`);
        if (ring) ring.classList.add('seen');
      }
    });
    // закрытие по Escape
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') this.close();
      if (e.key === 'ArrowRight') this.next();
      if (e.key === 'ArrowLeft') this.prev();
    });
  },

  open(storyIdx) {
    if (!this._stories.length) return;
    const story = this._stories[storyIdx];
    if (!story || !story.slides || !story.slides.length) return;  // нет слайдов — не открываем
    this._storyIdx = storyIdx;
    this._slideIdx = 0;
    document.getElementById('sv-bg').style.display = 'flex';
    document.body.style.overflow = 'hidden';
    this._render();
    this._startTimer();
    this._markSeen(this._stories[storyIdx].id, storyIdx);
  },

  close() {
    document.getElementById('sv-bg').style.display = 'none';
    document.body.style.overflow = '';
    this._stopTimer();
  },

  next() {
    const story = this._stories[this._storyIdx];
    if (this._slideIdx < story.slides.length - 1) {
      this._slideIdx++;
      this._render();
      this._startTimer();
    } else if (this._storyIdx < this._stories.length - 1) {
      this._storyIdx++;
      this._slideIdx = 0;
      this._render();
      this._startTimer();
      this._markSeen(this._stories[this._storyIdx].id, this._storyIdx);
    } else {
      this.close();
    }
  },

  prev() {
    if (this._slideIdx > 0) {
      this._slideIdx--;
    } else if (this._storyIdx > 0) {
      this._storyIdx--;
      this._slideIdx = this._stories[this._storyIdx].slides.length - 1;
      this._markSeen(this._stories[this._storyIdx].id, this._storyIdx);
    }
    this._render();
    this._startTimer();
  },

  _render() {
    const story = this._stories[this._storyIdx];
    const slide = story.slides[this._slideIdx];
    if (!slide) { this.close(); return; }

    // прогресс-полоски
    const prog = document.getElementById('sv-progress');
    prog.innerHTML = story.slides.map((_, i) =>
      `<div class="sv-bar"><div class="sv-bar-fill" id="sv-fill-${i}"
        style="width:${i < this._slideIdx ? '100' : '0'}%"></div></div>`
    ).join('');

    // слайд
    const color = slide.text_color || '#ffffff';
    let btnHtml = '';
    if (slide.btn_label && slide.btn_url) {
      const href = normalizeUrl(slide.btn_url);
      // внешние ссылки открываем в новой вкладке
      const external = /^https?:\/\//i.test(href);
      const attrs = external ? ' target="_blank" rel="noopener"' : '';
      btnHtml = `<a class="sv-btn" href="${esc(href)}"${attrs}>${esc(slide.btn_label)}</a>`;
    }
    document.getElementById('sv-slide-container').innerHTML = `
      <div class="sv-slide">
        <img src="/media/${esc(slide.image_url)}" alt="">
        <div class="sv-gradient"></div>
        <div class="sv-content" style="color:${esc(color)}">
          ${slide.text ? `<p>${esc(slide.text)}</p>` : ''}
          ${btnHtml}
        </div>
      </div>`;
  },

  _startTimer() {
    this._stopTimer();
    // анимируем текущую полоску
    const fill = document.getElementById(`sv-fill-${this._slideIdx}`);
    if (fill) {
      fill.style.transition = 'none';
      fill.style.width = '0%';
      requestAnimationFrame(() => {
        fill.style.transition = `width ${this._DURATION}ms linear`;
        fill.style.width = '100%';
      });
    }
    this._timer = setTimeout(() => this.next(), this._DURATION);
  },

  _stopTimer() {
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
  },

  _markSeen(storyId, ringIdx) {
    this._seen.add(storyId);
    localStorage.setItem('vkusno_seen_stories', JSON.stringify([...this._seen]));
    const ring = document.getElementById(`story-ring-${ringIdx}`);
    if (ring) ring.classList.add('seen');
  },
};

document.addEventListener('DOMContentLoaded', () => storyViewer.init());
