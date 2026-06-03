import json
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

import anthropic as _anthropic_lib

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bearpaw-secret-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bearpaw.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ── Load menu data ────────────────────────────────────────────────────────────

_menu_path = os.path.join(os.path.dirname(__file__), 'bearpaw-menu.json')
with open(_menu_path) as f:
    _data = json.load(f)

BRAND        = _data['brand']
ALLERGEN_KEY = _data['allergenKey']
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'bearpaw2024')

SHOPS = {
    loc['id']: {
        'name':    loc['name'],
        'address': loc['address'],
        'phone':   loc['phone'],
        'hours':   loc['hours'],
    }
    for loc in _data['locations']
}

# Per-location menus: { shop_id: [category_dict, ...] }
MENUS = {loc['id']: loc['menu']['categories'] for loc in _data['locations']}


def get_shop_item(shop, item_id):
    for cat in MENUS.get(shop, []):
        for item in cat.get('items', []):
            if item.get('id') == item_id:
                return {**item, 'category': cat['name']}
    return None


def allergen_names(codes):
    if not isinstance(codes, list):
        return []
    return [ALLERGEN_KEY.get(str(c), str(c)) for c in codes]


# ── Models ────────────────────────────────────────────────────────────────────

class Order(db.Model):
    id             = db.Column(db.String(8),   primary_key=True)
    shop           = db.Column(db.String(50),  nullable=False)
    customer_name  = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(100), nullable=False)
    customer_phone = db.Column(db.String(20),  nullable=True)
    pickup_time    = db.Column(db.String(50),  nullable=False)
    payment_method = db.Column(db.String(20),  nullable=False)
    payment_status = db.Column(db.String(20),  default='pending')
    status         = db.Column(db.String(20),  default='received')
    notes          = db.Column(db.Text,        nullable=True)
    total          = db.Column(db.Float,       nullable=False)
    created_at     = db.Column(db.DateTime,    default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id':             self.id,
            'shop':           self.shop,
            'customer_name':  self.customer_name,
            'customer_email': self.customer_email,
            'customer_phone': self.customer_phone,
            'pickup_time':    self.pickup_time,
            'payment_method': self.payment_method,
            'payment_status': self.payment_status,
            'status':         self.status,
            'notes':          self.notes,
            'total':          self.total,
            'created_at':     self.created_at.strftime('%H:%M'),
            'created_date':   self.created_at.strftime('%d %b'),
            'items':          [i.to_dict() for i in self.items],
        }


class OrderItem(db.Model):
    id            = db.Column(db.Integer,     primary_key=True)
    order_id      = db.Column(db.String(8),   db.ForeignKey('order.id'), nullable=False)
    item_id       = db.Column(db.String(50),  nullable=False)
    item_name     = db.Column(db.String(100), nullable=False)
    item_category = db.Column(db.String(100), nullable=False)
    price         = db.Column(db.Float,       nullable=False)
    quantity      = db.Column(db.Integer,     default=1)
    notes         = db.Column(db.Text,        nullable=True)

    def to_dict(self):
        return {
            'item_name':     self.item_name,
            'item_category': self.item_category,
            'price':         self.price,
            'quantity':      self.quantity,
            'notes':         self.notes,
            'subtotal':      self.price * self.quantity,
        }


class SoldOutItem(db.Model):
    shop    = db.Column(db.String(50), primary_key=True)
    item_id = db.Column(db.String(50), primary_key=True)


class HiddenItem(db.Model):
    shop    = db.Column(db.String(50), primary_key=True)
    item_id = db.Column(db.String(50), primary_key=True)


class DisabledSlot(db.Model):
    shop     = db.Column(db.String(50), primary_key=True)
    time_str = db.Column(db.String(5),  primary_key=True)


# ── Cart helpers ──────────────────────────────────────────────────────────────

def get_cart():
    return session.get('cart', {'shop': None, 'items': []})

def save_cart(cart):
    session['cart'] = cart
    session.modified = True

def cart_total(cart):
    return sum(i['price'] * i['quantity'] for i in cart['items'])

def cart_count(cart):
    return sum(i['quantity'] for i in cart['items'])


# ── Customer routes ───────────────────────────────────────────────────────────

@app.route('/')
def index():
    cart = get_cart()
    return render_template('index.html', shops=SHOPS, brand=BRAND, cart_count=cart_count(cart))


@app.route('/menu/<shop>')
def menu(shop):
    if shop not in SHOPS:
        return redirect(url_for('index'))
    cart = get_cart()
    conflict = cart['shop'] and cart['shop'] != shop and bool(cart['items'])
    sold_out_ids = {s.item_id for s in SoldOutItem.query.filter_by(shop=shop).all()}
    hidden_ids   = {h.item_id for h in HiddenItem.query.filter_by(shop=shop).all()}
    return render_template('menu.html',
                           shop=shop, shop_info=SHOPS[shop],
                           categories=MENUS[shop],
                           allergen_key=ALLERGEN_KEY,
                           cart=cart,
                           cart_count=cart_count(cart),
                           cart_shop_conflict=conflict,
                           sold_out_ids=sold_out_ids,
                           hidden_ids=hidden_ids,
                           shops=SHOPS)


@app.route('/cart/add', methods=['POST'])
def add_to_cart():
    data      = request.get_json()
    item_id   = data.get('item_id')
    shop      = data.get('shop')
    item_note = data.get('notes', '')

    if shop not in SHOPS:
        return jsonify({'success': False, 'error': 'Invalid shop'}), 400

    item = get_shop_item(shop, item_id)
    if not item:
        return jsonify({'success': False, 'error': 'Item not found'}), 400

    if SoldOutItem.query.filter_by(shop=shop, item_id=item_id).first():
        return jsonify({'success': False, 'error': 'Item is currently sold out'}), 400

    cart = get_cart()
    if cart['shop'] and cart['shop'] != shop:
        cart = {'shop': shop, 'items': []}
    cart['shop'] = shop

    for ci in cart['items']:
        if ci['id'] == item_id and ci.get('notes', '') == item_note:
            ci['quantity'] += 1
            save_cart(cart)
            return jsonify({'success': True, 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})

    cart['items'].append({
        'id': item_id, 'name': item['name'], 'category': item['category'],
        'price': item['price'], 'quantity': 1, 'notes': item_note,
    })
    save_cart(cart)
    return jsonify({'success': True, 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})


@app.route('/cart/remove', methods=['POST'])
def remove_from_cart():
    data  = request.get_json()
    index = data.get('index')
    cart  = get_cart()
    if 0 <= index < len(cart['items']):
        cart['items'].pop(index)
        if not cart['items']:
            cart['shop'] = None
    save_cart(cart)
    return jsonify({'success': True, 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})


@app.route('/cart/update', methods=['POST'])
def update_cart():
    data     = request.get_json()
    index    = data.get('index')
    quantity = data.get('quantity', 1)
    cart     = get_cart()
    if 0 <= index < len(cart['items']):
        if quantity <= 0:
            cart['items'].pop(index)
            if not cart['items']:
                cart['shop'] = None
        else:
            cart['items'][index]['quantity'] = quantity
    save_cart(cart)
    return jsonify({'success': True, 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})


@app.route('/cart/clear', methods=['POST'])
def clear_cart():
    session.pop('cart', None)
    return jsonify({'success': True})


@app.route('/cart')
def cart_page():
    cart      = get_cart()
    shop_info = SHOPS.get(cart['shop']) if cart['shop'] else None
    return render_template('cart.html', cart=cart, shop_info=shop_info,
                           cart_total=cart_total(cart), cart_count=cart_count(cart),
                           shops=SHOPS)


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cart = get_cart()
    if not cart['items']:
        return redirect(url_for('index'))

    shop      = cart['shop']
    shop_info = SHOPS[shop]

    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        email          = request.form.get('email', '').strip()
        phone          = request.form.get('phone', '').strip()
        pickup_time    = request.form.get('pickup_time', '').strip()
        payment_method = request.form.get('payment_method', 'pickup')
        notes          = request.form.get('notes', '').strip()

        if not name or not email or not pickup_time:
            flash('Please fill in all required fields.', 'error')
            return render_template('checkout.html', cart=cart, shop_info=shop_info,
                                   cart_total=cart_total(cart), cart_count=cart_count(cart),
                                   pickup_options=_shop_pickup_slots(shop), form_data=request.form)

        order_id = str(uuid.uuid4())[:8].upper()
        order = Order(
            id=order_id, shop=shop,
            customer_name=name, customer_email=email, customer_phone=phone,
            pickup_time=pickup_time, payment_method=payment_method,
            payment_status='paid' if payment_method == 'advance' else 'pending',
            notes=notes, total=cart_total(cart),
        )
        db.session.add(order)
        for ci in cart['items']:
            db.session.add(OrderItem(
                order_id=order_id, item_id=ci['id'],
                item_name=ci['name'], item_category=ci['category'],
                price=ci['price'], quantity=ci['quantity'],
                notes=ci.get('notes', ''),
            ))
        db.session.commit()

        socketio.emit('new_order', order.to_dict(), room=shop)
        session.pop('cart', None)
        return redirect(url_for('confirmation', order_id=order_id))

    return render_template('checkout.html', cart=cart, shop_info=shop_info,
                           cart_total=cart_total(cart), cart_count=cart_count(cart),
                           pickup_options=_shop_pickup_slots(shop), form_data={})


def _slot_times(shop):
    """All 15-min slots within today's opening hours (pure, no DB)."""
    today = datetime.now()
    day = today.strftime('%A').lower()
    hours_str = SHOPS[shop]['hours'].get(day, 'closed')
    if hours_str == 'closed':
        return []
    open_str, close_str = hours_str.split('-')
    oh, om = map(int, open_str.split(':'))
    ch, cm = map(int, close_str.split(':'))
    open_dt  = today.replace(hour=oh, minute=om, second=0, microsecond=0)
    close_dt = today.replace(hour=ch, minute=cm, second=0, microsecond=0)
    # Round open up to next 15-min boundary
    start = open_dt
    if start.minute % 15 != 0:
        start += timedelta(minutes=15 - start.minute % 15)
        start = start.replace(second=0, microsecond=0)
    last = close_dt - timedelta(minutes=15)
    slots, t = [], start
    while t <= last:
        slots.append(t.strftime('%H:%M'))
        t += timedelta(minutes=15)
    return slots


def _shop_pickup_slots(shop):
    """Enabled slots at least 20 min from now (for customer checkout)."""
    now      = datetime.now()
    disabled = {s.time_str for s in DisabledSlot.query.filter_by(shop=shop).all()}
    cutoff   = now + timedelta(minutes=20)
    result   = []
    for ts in _slot_times(shop):
        if ts in disabled:
            continue
        h, m = map(int, ts.split(':'))
        if now.replace(hour=h, minute=m, second=0, microsecond=0) >= cutoff:
            result.append({'value': ts, 'label': ts})
    return result


def _all_shop_slots(shop):
    """All slots for today with disabled state (for admin panel)."""
    disabled = {s.time_str for s in DisabledSlot.query.filter_by(shop=shop).all()}
    return [{'time': ts, 'disabled': ts in disabled} for ts in _slot_times(shop)]


@app.route('/confirmation/<order_id>')
def confirmation(order_id):
    order     = Order.query.get_or_404(order_id)
    shop_info = SHOPS[order.shop]
    return render_template('confirmation.html', order=order, shop_info=shop_info, cart_count=0)


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route('/admin/<shop>')
def admin_login(shop):
    if shop not in SHOPS:
        return redirect(url_for('index'))
    if session.get('admin_shop') == shop:
        return redirect(url_for('admin_dashboard', shop=shop))
    return render_template('admin/login.html', shop=shop, shop_info=SHOPS[shop])


@app.route('/admin/<shop>/login', methods=['POST'])
def admin_login_post(shop):
    if shop not in SHOPS:
        return redirect(url_for('index'))
    if request.form.get('password', '') == ADMIN_PASSWORD:
        session['admin_shop'] = shop
        return redirect(url_for('admin_dashboard', shop=shop))
    flash('Incorrect password. Please try again.', 'error')
    return render_template('admin/login.html', shop=shop, shop_info=SHOPS[shop])


@app.route('/admin/<shop>/dashboard')
def admin_dashboard(shop):
    if shop not in SHOPS:
        return redirect(url_for('index'))
    if session.get('admin_shop') != shop:
        return redirect(url_for('admin_login', shop=shop))

    today  = datetime.utcnow().date()
    orders = (Order.query
              .filter(Order.shop == shop, db.func.date(Order.created_at) == today)
              .order_by(Order.created_at.desc()).all())
    sold_out_ids = {s.item_id for s in SoldOutItem.query.filter_by(shop=shop).all()}
    hidden_ids   = {h.item_id for h in HiddenItem.query.filter_by(shop=shop).all()}
    return render_template('admin/dashboard.html',
                           shop=shop, shop_info=SHOPS[shop],
                           orders=orders, cart_count=0,
                           categories=MENUS[shop],
                           sold_out_ids=sold_out_ids,
                           hidden_ids=hidden_ids,
                           all_slots=_all_shop_slots(shop))


@app.route('/admin/order/<order_id>/status', methods=['POST'])
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    if session.get('admin_shop') != order.shop:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    new_status = request.get_json().get('status')
    if new_status not in ('received', 'preparing', 'ready', 'collected'):
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    order.status = new_status
    db.session.commit()
    socketio.emit('order_updated', {'order_id': order_id, 'status': new_status}, room=order.shop)
    return jsonify({'success': True, 'status': new_status})


@app.route('/admin/<shop>/hide', methods=['POST'])
def toggle_hidden(shop):
    if shop not in SHOPS or session.get('admin_shop') != shop:
        return jsonify({'success': False}), 403
    item_id = request.get_json().get('item_id')
    if not item_id:
        return jsonify({'success': False}), 400
    existing = HiddenItem.query.filter_by(shop=shop, item_id=item_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'success': True, 'hidden': False})
    db.session.add(HiddenItem(shop=shop, item_id=item_id))
    db.session.commit()
    return jsonify({'success': True, 'hidden': True})


@app.route('/admin/<shop>/slots', methods=['POST'])
def toggle_slot(shop):
    if shop not in SHOPS or session.get('admin_shop') != shop:
        return jsonify({'success': False}), 403
    time_str = request.get_json().get('time')
    if not time_str:
        return jsonify({'success': False}), 400
    existing = DisabledSlot.query.filter_by(shop=shop, time_str=time_str).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'success': True, 'disabled': False})
    db.session.add(DisabledSlot(shop=shop, time_str=time_str))
    db.session.commit()
    return jsonify({'success': True, 'disabled': True})


@app.route('/admin/<shop>/soldout', methods=['POST'])
def toggle_sold_out(shop):
    if shop not in SHOPS or session.get('admin_shop') != shop:
        return jsonify({'success': False}), 403
    item_id = request.get_json().get('item_id')
    if not item_id:
        return jsonify({'success': False}), 400
    existing = SoldOutItem.query.filter_by(shop=shop, item_id=item_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'success': True, 'sold_out': False})
    db.session.add(SoldOutItem(shop=shop, item_id=item_id))
    db.session.commit()
    return jsonify({'success': True, 'sold_out': True})


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_shop', None)
    return redirect(url_for('index'))


# ── SocketIO ──────────────────────────────────────────────────────────────────

@socketio.on('join_shop')
def on_join_shop(data):
    shop = data.get('shop')
    if shop in SHOPS:
        join_room(shop)
        emit('joined', {'shop': shop})


# ── Chat / AI ordering agent ─────────────────────────────────────────────────

_anthropic_client = None

def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        key = os.environ.get('ANTHROPIC_API_KEY')
        if key:
            _anthropic_client = _anthropic_lib.Anthropic(api_key=key)
    return _anthropic_client


def _menu_context(shop):
    sold_out_ids = {s.item_id for s in SoldOutItem.query.filter_by(shop=shop).all()}
    hidden_ids   = {h.item_id for h in HiddenItem.query.filter_by(shop=shop).all()}
    lines = []
    for cat in MENUS[shop]:
        visible = [i for i in cat.get('items', []) if i['id'] not in hidden_ids]
        if not visible:
            continue
        lines.append(f"\n### {cat['name']}")
        for item in visible:
            status = ' [SOLD OUT — cannot be ordered]' if item['id'] in sold_out_ids else ''
            desc = f" — {item['description']}" if item.get('description') else ''
            a = item.get('allergens', [])
            if isinstance(a, list) and a:
                allergen_str = ' | Contains: ' + ', '.join(ALLERGEN_KEY.get(str(x), str(x)) for x in a)
            elif isinstance(a, str) and a:
                allergen_str = f' | Allergens: {a}'
            else:
                allergen_str = ''
            dietary = (' | ' + ', '.join(item['dietary'])) if item.get('dietary') else ''
            lines.append(f"- **{item['name']}** — €{item['price']:.2f}{status}{desc}{allergen_str}{dietary} [id:{item['id']}]")
    return '\n'.join(lines)


def _agent_execute_tool(shop, name, inputs):
    if name == 'add_to_cart':
        item_id  = inputs.get('item_id', '')
        quantity = max(1, int(inputs.get('quantity', 1)))
        notes    = inputs.get('notes', '')
        if SoldOutItem.query.filter_by(shop=shop, item_id=item_id).first():
            return 'This item is currently sold out and cannot be added.'
        item = get_shop_item(shop, item_id)
        if not item:
            return f"Item id '{item_id}' not found on the menu."
        cart = get_cart()
        if cart['shop'] and cart['shop'] != shop:
            cart = {'shop': shop, 'items': []}
        cart['shop'] = shop
        for ci in cart['items']:
            if ci['id'] == item_id and ci.get('notes', '') == notes:
                ci['quantity'] += quantity
                save_cart(cart)
                return f"Added {quantity}× {item['name']} (already in cart, now {ci['quantity']} total). Cart total: €{cart_total(cart):.2f}."
        cart['items'].append({'id': item_id, 'name': item['name'], 'category': item['category'],
                              'price': item['price'], 'quantity': quantity, 'notes': notes})
        save_cart(cart)
        return f"Added {quantity}× {item['name']} @ €{item['price']:.2f} each. Cart total: €{cart_total(cart):.2f} ({cart_count(cart)} items)."

    if name == 'view_cart':
        cart = get_cart()
        if not cart['items']:
            return 'Cart is empty.'
        rows = [f"{i+1}. {ci['quantity']}× {ci['name']} @ €{ci['price']:.2f}" +
                (f" (Note: {ci['notes']})" if ci.get('notes') else '')
                for i, ci in enumerate(cart['items'])]
        rows.append(f"Total: €{cart_total(cart):.2f}")
        return '\n'.join(rows)

    if name == 'remove_from_cart':
        pos  = int(inputs.get('position', 1))
        cart = get_cart()
        idx  = pos - 1
        if 0 <= idx < len(cart['items']):
            removed = cart['items'].pop(idx)
            if not cart['items']:
                cart['shop'] = None
            save_cart(cart)
            return f"Removed {removed['name']}. Cart now: {cart_count(cart)} items, €{cart_total(cart):.2f}."
        return 'Invalid position.'

    return 'Unknown tool.'


_AGENT_TOOLS = [
    {
        'name': 'add_to_cart',
        'description': 'Add a menu item to the customer\'s cart. Use the item\'s id from the menu listing.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'item_id': {'type': 'string', 'description': 'The item id (e.g. "the-goat")'},
                'quantity': {'type': 'integer', 'description': 'How many to add (default 1)', 'default': 1},
                'notes': {'type': 'string', 'description': 'Special requests, e.g. "no mayo"', 'default': ''},
            },
            'required': ['item_id'],
        },
    },
    {
        'name': 'view_cart',
        'description': 'Show the current contents of the customer\'s cart.',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'remove_from_cart',
        'description': 'Remove an item from the cart by its 1-indexed position (use view_cart first to check positions).',
        'input_schema': {
            'type': 'object',
            'properties': {
                'position': {'type': 'integer', 'description': 'Position of the item in the cart (1 = first item)'},
            },
            'required': ['position'],
        },
    },
]


@app.route('/chat/<shop>', methods=['POST'])
def chat(shop):
    if shop not in SHOPS:
        return jsonify({'error': 'Invalid shop'}), 400

    client = _get_anthropic()
    if not client:
        return jsonify({'reply': "The ordering assistant isn't set up yet — please order using the menu below!", 'cart_count': cart_count(get_cart()), 'cart_total': cart_total(get_cart())})

    data     = request.get_json()
    messages = list(data.get('messages', []))
    if not messages:
        return jsonify({'error': 'No messages'}), 400

    si = SHOPS[shop]
    slots_text = ', '.join(s['value'] for s in _shop_pickup_slots(shop)) or 'No slots currently available'

    system = f"""You are a friendly ordering assistant for Bear Paw {si['name']}, a beloved deli at {si['address']}.

Help customers browse the menu, answer questions about ingredients and allergens, and build their order using the provided tools. Be warm, concise and helpful. Don't write long lists unprompted — answer what was asked.

RULES:
- Never add a [SOLD OUT] item to the cart.
- Confirm each item added and show the running total.
- When the customer is done, tell them to click the basket / go to /checkout to complete their order.
- Keep replies short (2-4 sentences max unless listing menu items).

Available pickup times today: {slots_text}

MENU — {si['name']}:
{_menu_context(shop)}
"""

    current = list(messages)
    for _ in range(6):
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            system=system,
            tools=_AGENT_TOOLS,
            messages=current,
        )

        if response.stop_reason == 'end_turn':
            text = next((b.text for b in response.content if hasattr(b, 'text')), '')
            cart = get_cart()
            return jsonify({'reply': text, 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})

        if response.stop_reason == 'tool_use':
            current.append({'role': 'assistant', 'content': response.content})
            results = [
                {'type': 'tool_result', 'tool_use_id': b.id,
                 'content': _agent_execute_tool(shop, b.name, b.input)}
                for b in response.content if b.type == 'tool_use'
            ]
            current.append({'role': 'user', 'content': results})
        else:
            break

    cart = get_cart()
    return jsonify({'reply': "Sorry, I couldn't process that — please try again.", 'cart_count': cart_count(cart), 'cart_total': cart_total(cart)})


# ── Bootstrap ─────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
