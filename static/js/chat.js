// Ordering assistant chat widget
const CHAT_SHOP = document.body.dataset.shop;
let chatHistory = [];  // [{role, content}] — plain text only, no tool internals
let chatOpen = false;

const fabBtn    = document.getElementById('chat-toggle-btn');
const panel     = document.getElementById('chat-panel');
const msgsEl    = document.getElementById('chat-messages');
const inputEl   = document.getElementById('chat-input');
const sendBtn   = document.getElementById('chat-send');
const closeBtn  = document.getElementById('chat-close');
const cartBar   = document.getElementById('chat-cart-summary');

fabBtn.addEventListener('click', () => togglePanel(true));
closeBtn.addEventListener('click', () => togglePanel(false));
sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function togglePanel(open) {
  chatOpen = open;
  panel.classList.toggle('open', open);
  if (open) {
    if (chatHistory.length === 0) greet();
    inputEl.focus();
  }
}

function greet() {
  appendMsg('assistant', "Hi! 👋 I can help you browse the menu and build your order. What can I get you?");
}

function appendMsg(role, text) {
  const div = document.createElement('div');
  div.className = `chat-msg chat-msg-${role}`;
  div.textContent = text;
  msgsEl.appendChild(div);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg-assistant chat-typing';
  div.id = 'chat-typing';
  div.innerHTML = '<span></span><span></span><span></span>';
  msgsEl.appendChild(div);
  msgsEl.scrollTop = msgsEl.scrollHeight;
}

function hideTyping() {
  document.getElementById('chat-typing')?.remove();
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || sendBtn.disabled) return;
  inputEl.value = '';

  appendMsg('user', text);
  chatHistory.push({role: 'user', content: text});

  sendBtn.disabled = true;
  showTyping();

  try {
    const res = await fetch(`/chat/${CHAT_SHOP}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({messages: chatHistory}),
    });
    hideTyping();
    if (!res.ok) throw new Error();
    const data = await res.json();
    const reply = data.reply || "Sorry, something went wrong.";
    appendMsg('assistant', reply);
    chatHistory.push({role: 'assistant', content: reply});
    syncCart(data.cart_count, data.cart_total);
  } catch {
    hideTyping();
    appendMsg('assistant', "Something went wrong — please try again.");
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

function syncCart(count, total) {
  if (count === undefined) return;

  // Update cart bar inside panel
  cartBar.textContent = count > 0 ? `🛒 ${count} item${count !== 1 ? 's' : ''} — €${Number(total).toFixed(2)}` : '';

  // Update basket badge in page header
  let badge = document.querySelector('.cart-badge');
  let navCart = document.querySelector('.nav-cart');
  if (count > 0) {
    if (badge) {
      badge.textContent = count;
    } else if (navCart) {
      navCart.innerHTML = `Basket <span class="cart-badge">${count}</span>`;
    } else {
      const a = document.createElement('a');
      a.href = '/cart';
      a.className = 'nav-cart';
      a.innerHTML = `Basket <span class="cart-badge">${count}</span>`;
      document.querySelector('.menu-header-inner')?.appendChild(a);
    }
  } else if (badge) {
    badge.textContent = '0';
  }
}
