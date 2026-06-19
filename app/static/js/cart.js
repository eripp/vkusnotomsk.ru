// ─── Корзина (localStorage) ──────────────────────────────────────────────────
const cart = {
  _key: 'vkusno_cart',

  get items() {
    try { return JSON.parse(localStorage.getItem(this._key) || '[]'); }
    catch { return []; }
  },

  _save(items) {
    localStorage.setItem(this._key, JSON.stringify(items));
    this._render();
  },

  addFromBtn(btn) {
    const b = btn.closest('[data-id]') || btn;
    this.add(
      parseInt(b.dataset.id),
      b.dataset.name || '',
      parseInt(b.dataset.price) || 0,
      b.dataset.image || '',
      b.dataset.weight || '',
    );
  },

  add(id, name, price, image, weight) {
    const items = this.items;
    const idx = items.findIndex(i => i.id === id);
    if (idx >= 0) { items[idx].qty += 1; }
    else { items.push({ id, name, price, image, weight, qty: 1 }); }
    this._save(items);
    this._animateBtn(id);
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ ecommerce: { add: { products: [{ id, name, price, quantity: 1 }] } } });
  },

  dec(id) {
    const items = this.items;
    const idx = items.findIndex(i => i.id === id);
    if (idx < 0) return;
    const wasLast = items[idx].qty <= 1;
    const item = items[idx];
    if (wasLast) { items.splice(idx, 1); }
    else { items[idx].qty -= 1; }
    this._save(items);
    if (wasLast) {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ ecommerce: { remove: { products: [{ id: item.id, name: item.name, price: item.price, quantity: 1 }] } } });
    }
  },

  remove(id) {
    this._save(this.items.filter(i => i.id !== id));
  },

  qty(id) {
    const item = this.items.find(i => i.id === id);
    return item ? item.qty : 0;
  },

  get total() { return this.items.reduce((s, i) => s + i.price * i.qty, 0); },
  get count() { return this.items.reduce((s, i) => s + i.qty, 0); },

  _render() {
    // счётчик в шапке
    const countEl = document.getElementById('cart-count');
    if (countEl) countEl.textContent = this.count;

    // Ищем только корневые кнопки плитки — те у которых есть data-price
    // (отличаем от вложенных кнопок + внутри счётчика)
    document.querySelectorAll('.add-btn[data-id], .add-btn-counter[data-id]').forEach(wrap => {
      const id  = parseInt(wrap.dataset.id);
      const qty = this.qty(id);
      const nm  = wrap.dataset.name  || '';
      const pr  = wrap.dataset.price || '0';
      const im  = wrap.dataset.image || '';
      const wt  = wrap.dataset.weight || '';

      if (qty === 0) {
        wrap.className = 'add-btn';
        wrap.setAttribute('onclick', 'event.stopPropagation(); cart.addFromBtn(this)');
        wrap.innerHTML = `<span id="add-btn-${id}">+</span>`;
      } else {
        wrap.className = 'add-btn-counter';
        wrap.removeAttribute('onclick');
        wrap.innerHTML = `
          <button onclick="event.stopPropagation(); cart.dec(${id})">−</button>
          <span class="qty">${qty}</span>
          <button onclick="event.stopPropagation(); cart.addFromBtn(this)"
            data-id="${id}" data-name="${nm}" data-price="${pr}"
            data-image="${im}" data-weight="${wt}">+</button>`;
      }
    });

    // тело корзины
    this._renderDrawer();
  },

  _renderDrawer() {
    const items = this.items;
    const emptyEl = document.getElementById('cart-empty');
    const itemsEl = document.getElementById('cart-items');
    const footerEl = document.getElementById('cart-footer');
    const subtotalEl = document.getElementById('cart-subtotal');
    const totalEl = document.getElementById('cart-total');
    if (!itemsEl) return;

    if (items.length === 0) {
      if (emptyEl) emptyEl.style.display = '';
      itemsEl.innerHTML = '';
      if (footerEl) footerEl.style.display = 'none';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';
    if (footerEl) footerEl.style.display = '';

    itemsEl.innerHTML = items.map(item => `
      <div class="cart-item" data-id="${item.id}">
        ${item.image
          ? `<img class="cart-item-img" src="${item.image}" alt="${item.name}">`
          : `<div class="cart-item-img-placeholder">🍱</div>`}
        <div class="cart-item-info">
          <div class="cart-item-name">${item.name}</div>
          ${item.weight ? `<div class="cart-item-weight">${item.weight}</div>` : ''}
          <div class="cart-item-footer">
            <div class="add-btn-counter" style="height:28px">
              <button onclick="cart.dec(${item.id})" style="width:24px">−</button>
              <span class="qty">${item.qty}</span>
              <button onclick="cart.inc(${item.id})" style="width:24px">+</button>
            </div>
            <span class="cart-item-price">${(item.price * item.qty).toLocaleString('ru')} ₽</span>
          </div>
        </div>
      </div>`).join('');

    const subtotal = this.total;
    if (subtotalEl) subtotalEl.textContent = subtotal.toLocaleString('ru') + ' ₽';
    if (totalEl) totalEl.textContent = subtotal.toLocaleString('ru') + ' ₽';
  },

  inc(id) {
    const items = this.items;
    const idx = items.findIndex(i => i.id === id);
    if (idx >= 0) { items[idx].qty += 1; this._save(items); }
  },

  _animateBtn(id) {
    const btn = document.querySelector(`[data-id="${id}"]`);
    if (!btn) return;
    btn.style.transform = 'scale(1.2)';
    setTimeout(() => btn.style.transform = '', 150);
  },
};

// ─── Drawer ───────────────────────────────────────────────────────────────────
const cartDrawer = {
  open() {
    document.getElementById('cart-drawer')?.classList.add('open');
    document.getElementById('cart-overlay')?.classList.add('open');
    document.body.style.overflow = 'hidden';
    cart._renderDrawer();
  },
  close() {
    document.getElementById('cart-drawer')?.classList.remove('open');
    document.getElementById('cart-overlay')?.classList.remove('open');
    document.body.style.overflow = '';
  },
};

// Инициализация при загрузке
document.addEventListener('DOMContentLoaded', () => cart._render());
