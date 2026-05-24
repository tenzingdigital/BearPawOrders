import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bearpaw-secret-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bearpaw.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

SHOPS = {
    'delgany':     {'name': 'Delgany',     'address': 'Main Street, Delgany, Co. Wicklow',     'phone': '01 234 5678'},
    'enniskerry':  {'name': 'Enniskerry',  'address': 'The Square, Enniskerry, Co. Wicklow',   'phone': '01 234 5679'},
    'greystones':  {'name': 'Greystones',  'address': 'Church Road, Greystones, Co. Wicklow',  'phone': '01 234 5680'},
}

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'bearpaw2024')

MENU = {
    'Classics': [
        {'id': 'blt',       'name': 'BLT',           'price': 7.50,  'description': 'Crispy bacon, fresh lettuce, vine tomato on sourdough', 'popular': True},
        {'id': 'club',      'name': 'Club Sandwich',  'price': 9.00,  'description': 'Triple decker with chicken, bacon, egg, lettuce & tomato'},
        {'id': 'tuna-melt', 'name': 'Tuna Melt',      'price': 8.50,  'description': 'Atlantic tuna mayo, melted mature cheddar on white'},
        {'id': 'ham-cheese','name': 'Ham & Cheese',   'price': 7.50,  'description': 'Honey glazed ham, mature cheddar, wholegrain mustard'},
        {'id': 'egg-mayo',  'name': 'Egg Mayo',        'price': 6.50,  'description': 'Free range egg mayo, watercress, cracked black pepper'},
    ],
    'Hot Sandwiches': [
        {'id': 'hot-chicken',    'name': 'Hot Press Chicken', 'price': 9.50,  'description': 'Grilled chicken, basil pesto, sun-dried tomato, mozzarella', 'popular': True},
        {'id': 'steak-ciabatta', 'name': 'Steak Ciabatta',   'price': 12.50, 'description': 'Sirloin steak strips, caramelised onions, horseradish, rocket'},
        {'id': 'meatball-sub',   'name': 'Meatball Sub',      'price': 10.00, 'description': 'Beef meatballs, marinara sauce, grated parmesan'},
        {'id': 'pulled-pork',    'name': 'Pulled Pork Roll',  'price': 10.50, 'description': '12hr slow cooked pork, apple slaw, BBQ sauce'},
    ],
    'Wraps': [
        {'id': 'caesar-wrap',  'name': 'Caesar Chicken Wrap', 'price': 8.50,  'description': 'Grilled chicken, romaine, parmesan, Caesar dressing'},
        {'id': 'falafel-wrap', 'name': 'Falafel & Hummus',    'price': 8.00,  'description': 'Crispy falafel, hummus, tabbouleh, tzatziki, pickled red onion'},
        {'id': 'prawn-wrap',   'name': 'Prawn Marie Rose',    'price': 9.50,  'description': 'Tiger prawns, marie rose, avocado, gem lettuce'},
        {'id': 'bbq-beef',     'name': 'BBQ Beef Wrap',       'price': 9.50,  'description': 'Pulled beef brisket, BBQ sauce, jalapeños, red onion, cheddar'},
    ],
    'Bear Paw Specials': [
        {'id': 'bear-special', 'name': 'Bear Paw Special',  'price': 11.00, 'description': "Today's signature creation — ask at the counter for today's filling!", 'popular': True},
        {'id': 'smashed-avo',  'name': 'Smashed Avo & Egg', 'price': 10.00, 'description': 'Sourdough, smashed avocado, poached egg, feta, chilli flakes'},
        {'id': 'caprese',      'name': 'Caprese Stack',      'price': 9.50,  'description': 'Buffalo mozzarella, heirloom tomato, fresh basil, aged balsamic'},
    ],
}

ALL_ITEMS = {
    item['id']: {**item, 'category': cat}
    for cat, items in MENU.items()
    for item in items
}


# ── Models ──────────────────────────────────────────────────────────────────

class Order(db.Model):
    id             = db.Column(db.String(8),   primary_key=True)
    shop           = db.Column(db.String(50),  nullable=False)
    customer_name  = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(100), nullable=False)
    customer_phone = db.Column(db.String(20),  nullable=True)
    pickup_time    = db.Column(db.String(50),  nullable=False)
    payment_method = db.Column(db.String(20),  nullable=False)  # 'pickup' | 'advance'
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


# ── Cart helpers ─────────────────────────────────────────────────────────────

def get_cart():
    return session.get('cart', {'shop': None, 'items': []})

def save_cart(cart):
    session['cart'] = cart
    session.modified = True

def cart_total(cart):
    return sum(i['price'] * i['quantity'] for i in cart['items'])

def cart_count(cart):
    return sum(i['quantity'] for i in cart['items'])


# ── Customer routes ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    cart = get_cart()
    return render_template('index.html', shops=SHOPS, cart_count=cart_count(cart))


@app.route('/menu/<shop>')
def menu(shop):
    if shop not in SHOPS:
        return redirect(url_for('index'))
    cart = get_cart()
    conflict = cart['shop'] and cart['shop'] != shop and bool(cart['items'])
    return render_template('menu.html',
                           shop=shop, shop_info=SHOPS[shop],
                           menu=MENU, cart=cart,
                           cart_count=cart_count(cart),
                           cart_shop_conflict=conflict)


@app.route('/cart/add', methods=['POST'])
def add_to_cart():
    data      = request.get_json()
    item_id   = data.get('item_id')
    shop      = data.get('shop')
    item_note = data.get('notes', '')

    if item_id not in ALL_ITEMS or shop not in SHOPS:
        return jsonify({'success': False, 'error': 'Invalid item or shop'}), 400

    cart = get_cart()
    if cart['shop'] and cart['shop'] != shop:
        cart = {'shop': shop, 'items': []}
    cart['shop'] = shop

    item = ALL_ITEMS[item_id]
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
                                   pickup_options=_pickup_options(), form_data=request.form)

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
                           pickup_options=_pickup_options(), form_data={})


def _pickup_options():
    now = datetime.now()
    options = []
    for mins in [20, 30, 45, 60, 75, 90]:
        t = now + timedelta(minutes=mins)
        options.append({'value': t.strftime('%H:%M'), 'label': f'{t.strftime("%H:%M")}  (~{mins} min)'})
    return options


@app.route('/confirmation/<order_id>')
def confirmation(order_id):
    order     = Order.query.get_or_404(order_id)
    shop_info = SHOPS[order.shop]
    return render_template('confirmation.html', order=order, shop_info=shop_info, cart_count=0)


# ── Admin routes ─────────────────────────────────────────────────────────────

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
    return render_template('admin/dashboard.html',
                           shop=shop, shop_info=SHOPS[shop],
                           orders=orders, cart_count=0)


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


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_shop', None)
    return redirect(url_for('index'))


# ── SocketIO ─────────────────────────────────────────────────────────────────

@socketio.on('join_shop')
def on_join_shop(data):
    shop = data.get('shop')
    if shop in SHOPS:
        join_room(shop)
        emit('joined', {'shop': shop})


# ── Bootstrap ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
