// Checkout page — payment method toggle & form submit
document.querySelectorAll('input[name="payment_method"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const advanceNote = document.getElementById('advance-note');
    if (advanceNote) {
      advanceNote.style.display = radio.value === 'advance' ? 'block' : 'none';
    }
  });
});

const form    = document.getElementById('checkout-form');
const submitBtn = document.getElementById('submit-btn');

form.addEventListener('submit', () => {
  submitBtn.disabled = true;
  submitBtn.textContent = 'Placing order…';
});
