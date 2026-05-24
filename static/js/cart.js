// Cart page interactions
function updateTotals(cartCount, cartTotal) {
  const totalEl = document.getElementById('cart-total');
  if (totalEl) totalEl.textContent = '€' + cartTotal.toFixed(2);

  const badge = document.querySelector('.cart-badge');
  if (badge) badge.textContent = cartCount;

  if (cartCount === 0) {
    window.location.reload();
  }
}

document.querySelectorAll('.qty-minus').forEach(btn => {
  btn.addEventListener('click', async () => {
    const index = parseInt(btn.dataset.index);
    const display = document.querySelector(`.qty-display[data-index="${index}"]`);
    const current = parseInt(display.textContent);
    const res  = await fetch('/cart/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index, quantity: current - 1}),
    });
    const data = await res.json();
    if (data.success) {
      if (current - 1 <= 0) {
        document.querySelector(`.cart-item[data-index="${index}"]`).remove();
      } else {
        display.textContent = current - 1;
        const priceEl = document.querySelector(`.cart-item-price[data-index="${index}"]`);
        const unitPrice = parseFloat(priceEl.dataset.unit);
        priceEl.textContent = '€' + (unitPrice * (current - 1)).toFixed(2);
      }
      updateTotals(data.cart_count, data.cart_total);
    }
  });
});

document.querySelectorAll('.qty-plus').forEach(btn => {
  btn.addEventListener('click', async () => {
    const index = parseInt(btn.dataset.index);
    const display = document.querySelector(`.qty-display[data-index="${index}"]`);
    const current = parseInt(display.textContent);
    const res  = await fetch('/cart/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index, quantity: current + 1}),
    });
    const data = await res.json();
    if (data.success) {
      display.textContent = current + 1;
      const priceEl = document.querySelector(`.cart-item-price[data-index="${index}"]`);
      const unitPrice = parseFloat(priceEl.dataset.unit);
      priceEl.textContent = '€' + (unitPrice * (current + 1)).toFixed(2);
      updateTotals(data.cart_count, data.cart_total);
    }
  });
});

document.querySelectorAll('.remove-item').forEach(btn => {
  btn.addEventListener('click', async () => {
    const index = parseInt(btn.dataset.index);
    const res  = await fetch('/cart/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index}),
    });
    const data = await res.json();
    if (data.success) {
      document.querySelector(`.cart-item[data-index="${index}"]`).remove();
      // Re-index remaining items
      document.querySelectorAll('.cart-item').forEach((el, i) => {
        el.dataset.index = i;
        el.querySelectorAll('[data-index]').forEach(child => child.dataset.index = i);
      });
      updateTotals(data.cart_count, data.cart_total);
    }
  });
});
