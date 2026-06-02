// Admin dashboard — real-time order updates via Socket.IO
const SHOP = document.body.dataset.shop;
const socket = io();

socket.emit('join_shop', {shop: SHOP});

// ── Stats helpers ────────────────────────────────────────────────────────────

function recalcStats() {
  const cards = document.querySelectorAll('.order-card');
  let total = cards.length, received = 0, preparing = 0, ready = 0;
  cards.forEach(c => {
    const s = c.dataset.status;
    if (s === 'received')  received++;
    if (s === 'preparing') preparing++;
    if (s === 'ready')     ready++;
  });
  document.getElementById('stat-total').textContent     = total;
  document.getElementById('stat-received').textContent  = received;
  document.getElementById('stat-preparing').textContent = preparing;
  document.getElementById('stat-ready').textContent     = ready;
}

// ── Status button handlers ───────────────────────────────────────────────────

function bindStatusButtons(card) {
  card.querySelectorAll('.status-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const orderId = btn.dataset.orderId;
      const status  = btn.dataset.status;
      const res = await fetch(`/admin/order/${orderId}/status`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status}),
      });
      if (res.ok) {
        applyStatus(card, status);
      }
    });
  });
}

function applyStatus(card, status) {
  card.dataset.status = status;

  // Update border via attribute (CSS handles it)
  const badge = card.querySelector('.status-badge');
  if (badge) {
    badge.className = `status-badge status-${status}`;
    badge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  }

  // Toggle active state on buttons
  card.querySelectorAll('.status-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.status === status);
  });

  recalcStats();
}

// Bind existing cards on page load
document.querySelectorAll('.order-card').forEach(bindStatusButtons);
recalcStats();

// ── Render a new order card ──────────────────────────────────────────────────

function renderOrderCard(order) {
  const payClass = order.payment_status === 'paid' ? 'payment-paid' : 'payment-pending';
  const payLabel = order.payment_status === 'paid' ? '✓ Paid online' : 'Pay on collection';

  const itemsHtml = order.items.map(i =>
    `<li><span>${i.quantity}× ${i.item_name}${i.notes ? ` <em>(${i.notes})</em>` : ''}</span>
         <span>€${(i.price * i.quantity).toFixed(2)}</span></li>`
  ).join('');

  const statusBtns = ['received','preparing','ready','collected'].map(s =>
    `<button class="status-btn${s === order.status ? ' active' : ''}"
             data-status="${s}" data-order-id="${order.id}">${s.charAt(0).toUpperCase()+s.slice(1)}</button>`
  ).join('');

  const div = document.createElement('div');
  div.className = 'order-card new-order-flash';
  div.dataset.orderId = order.id;
  div.dataset.status  = order.status;
  div.innerHTML = `
    <div class="order-card-header">
      <div class="order-meta">
        <span class="order-id">#${order.id}</span>
        <span class="status-badge status-${order.status}">${order.status.charAt(0).toUpperCase()+order.status.slice(1)}</span>
      </div>
      <span class="order-time">${order.created_at}</span>
    </div>
    <div class="order-customer">${order.customer_name}</div>
    <div class="order-pickup">🕐 Pickup: ${order.pickup_time} · ${order.customer_phone || order.customer_email}</div>
    <ul class="order-items-list">${itemsHtml}</ul>
    ${order.notes ? `<p style="font-size:.82rem;color:var(--warm-gray);margin-bottom:10px">📝 ${order.notes}</p>` : ''}
    <div class="order-footer">
      <div style="display:flex;align-items:center;gap:10px">
        <span class="order-total">€${order.total.toFixed(2)}</span>
        <span class="payment-badge ${payClass}">${payLabel}</span>
      </div>
      <div class="status-buttons">${statusBtns}</div>
    </div>`;

  return div;
}

// ── Socket.IO events ─────────────────────────────────────────────────────────

socket.on('new_order', order => {
  const list = document.getElementById('orders-list');
  const empty = document.getElementById('no-orders');
  if (empty) empty.remove();

  const card = renderOrderCard(order);
  list.prepend(card);
  bindStatusButtons(card);
  recalcStats();

  // Browser notification
  if (Notification.permission === 'granted') {
    new Notification(`🐾 New order #${order.id}`, {
      body: `${order.customer_name} — €${order.total.toFixed(2)}`,
    });
  }
});

socket.on('order_updated', data => {
  const card = document.querySelector(`.order-card[data-order-id="${data.order_id}"]`);
  if (card) applyStatus(card, data.status);
});

// Request notification permission
if ('Notification' in window && Notification.permission === 'default') {
  Notification.requestPermission();
}

// ── Sold-out toggles ─────────────────────────────────────────────────────────

document.querySelectorAll('.soldout-toggle-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const itemId = btn.dataset.itemId;
    const res = await fetch(`/admin/${SHOP}/soldout`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({item_id: itemId}),
    });
    if (!res.ok) return;
    const data = await res.json();
    const row = btn.closest('.soldout-item');
    const tag = row.querySelector('.item-status-tag');
    if (data.sold_out) {
      row.classList.add('is-soldout');
      btn.classList.add('soldout-active');
      btn.textContent = 'Back in stock';
      if (tag) { tag.className = 'item-status-tag tag-soldout'; tag.textContent = 'Sold out'; }
      else {
        const nameEl = row.querySelector('.soldout-item-name');
        nameEl.insertAdjacentHTML('beforeend', '<span class="item-status-tag tag-soldout">Sold out</span>');
      }
    } else {
      row.classList.remove('is-soldout');
      btn.classList.remove('soldout-active');
      btn.textContent = 'Mark sold out';
      if (tag) tag.remove();
    }
  });
});

// ── Hide (remove from menu) toggles ──────────────────────────────────────────

document.querySelectorAll('.hide-toggle-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const itemId = btn.dataset.itemId;
    const res = await fetch(`/admin/${SHOP}/hide`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({item_id: itemId}),
    });
    if (!res.ok) return;
    const data = await res.json();
    const row = btn.closest('.soldout-item');
    const soldoutBtn = row.querySelector('.soldout-toggle-btn');
    const tag = row.querySelector('.item-status-tag');
    if (data.hidden) {
      row.classList.add('is-hidden');
      row.classList.remove('is-soldout');
      btn.classList.add('hide-active');
      btn.textContent = 'Restore to menu';
      if (soldoutBtn) soldoutBtn.style.display = 'none';
      if (tag) { tag.className = 'item-status-tag tag-hidden'; tag.textContent = 'Hidden'; }
      else {
        const nameEl = row.querySelector('.soldout-item-name');
        nameEl.insertAdjacentHTML('beforeend', '<span class="item-status-tag tag-hidden">Hidden</span>');
      }
    } else {
      row.classList.remove('is-hidden');
      btn.classList.remove('hide-active');
      btn.textContent = 'Remove from menu';
      if (soldoutBtn) soldoutBtn.style.display = '';
      if (tag) tag.remove();
    }
  });
});

// ── Pickup slot toggles ───────────────────────────────────────────────────────

document.querySelectorAll('.slot-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const time = btn.dataset.time;
    const res = await fetch(`/admin/${SHOP}/slots`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({time}),
    });
    if (!res.ok) return;
    const data = await res.json();
    btn.classList.toggle('slot-disabled', data.disabled);
  });
});
