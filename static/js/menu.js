// Menu page — add to cart
const SHOP = document.body.dataset.shop;

const toast = document.getElementById('toast');
let toastTimer;

function showToast(msg) {
  toast.querySelector('.toast-msg').textContent = msg;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
}

function updateNavCart(count) {
  const nav = document.querySelector('.nav-cart');
  if (!nav) {
    if (count > 0) {
      const header = document.querySelector('.nav');
      const a = document.createElement('a');
      a.href = '/cart';
      a.className = 'nav-cart';
      a.innerHTML = `<span class="cart-icon">🛒</span><span class="cart-badge">${count}</span>`;
      header.appendChild(a);
    }
    return;
  }
  const badge = nav.querySelector('.cart-badge');
  if (badge) badge.textContent = count;
  if (count === 0) nav.remove();
}

document.querySelectorAll('.add-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const itemId = btn.dataset.itemId;
    const name   = btn.dataset.name;

    btn.disabled = true;
    btn.textContent = '…';

    const res  = await fetch('/cart/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({item_id: itemId, shop: SHOP, notes: ''}),
    });
    const data = await res.json();

    if (data.success) {
      btn.textContent = 'Added ✓';
      btn.classList.add('added');
      updateNavCart(data.cart_count);
      showToast(`${name} added to cart`);
      setTimeout(() => {
        btn.textContent = 'Add to cart';
        btn.classList.remove('added');
        btn.disabled = false;
      }, 1500);
    } else {
      btn.textContent = 'Add to cart';
      btn.disabled = false;
      if (data.error === 'shop_conflict') {
        showToast('Cart cleared — different shop');
      }
    }
  });
});

// clear cart conflict
const clearBtn = document.getElementById('clear-conflict');
if (clearBtn) {
  clearBtn.addEventListener('click', async () => {
    await fetch('/cart/clear', {method: 'POST'});
    document.getElementById('conflict-banner').remove();
  });
}
