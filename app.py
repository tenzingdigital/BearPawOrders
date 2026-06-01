import csv
import io
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash

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


class Staff(db.Model):
    id            = db.Column(db.Integer,     primary_key=True)
    shop          = db.Column(db.String(50),  nullable=False)
    name          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(100), nullable=True)
    role          = db.Column(db.String(20),  default='staff')    # 'manager' | 'staff'
    pin_hash      = db.Column(db.String(256), nullable=True)
    status        = db.Column(db.String(20),  default='invited')  # 'invited' | 'active'
    joined_at     = db.Column(db.DateTime,    nullable=True)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)


class ShopInviteLink(db.Model):
    id      = db.Column(db.Integer,    primary_key=True)
    shop    = db.Column(db.String(50), unique=True, nullable=False)
    token   = db.Column(db.String(64), unique=True, nullable=False)
    expires = db.Column(db.DateTime,   nullable=False)


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
    return render_template('menu.html',
                           shop=shop, shop_info=SHOPS[shop],
                           categories=MENUS[shop],
                           allergen_key=ALLERGEN_KEY,
                           cart=cart,
                           cart_count=cart_count(cart),
                           cart_shop_conflict=conflict,
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


# ── Team management routes ────────────────────────────────────────────────────

def _admin_required(shop):
    return shop in SHOPS and session.get('admin_shop') == shop


@app.route('/admin/<shop>/team')
def admin_team(shop):
    if not _admin_required(shop):
        return redirect(url_for('admin_login', shop=shop))
    staff  = Staff.query.filter_by(shop=shop).order_by(Staff.created_at.desc()).all()
    invite = ShopInviteLink.query.filter_by(shop=shop).first()
    return render_template('admin/team.html', shop=shop, shop_info=SHOPS[shop],
                           staff=staff, invite=invite, cart_count=0)


@app.route('/admin/<shop>/team/generate-link', methods=['POST'])
def team_generate_link(shop):
    if not _admin_required(shop):
        return jsonify({'success': False}), 403
    token   = secrets.token_hex(32)
    expires = datetime.utcnow() + timedelta(days=30)
    invite  = ShopInviteLink.query.filter_by(shop=shop).first()
    if invite:
        invite.token   = token
        invite.expires = expires
    else:
        db.session.add(ShopInviteLink(shop=shop, token=token, expires=expires))
    db.session.commit()
    join_url = request.host_url.rstrip('/') + url_for('join_link', token=token)
    return jsonify({'success': True, 'url': join_url,
                    'expires': expires.strftime('%d %b %Y')})


@app.route('/admin/<shop>/team/add', methods=['POST'])
def team_add_staff(shop):
    if not _admin_required(shop):
        return jsonify({'success': False}), 403
    data  = request.get_json()
    name  = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    role  = data.get('role', 'staff')
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    s = Staff(shop=shop, name=name, email=email or None, role=role)
    db.session.add(s)
    db.session.commit()
    return jsonify({'success': True, 'staff': {
        'id': s.id, 'name': s.name, 'email': s.email or '',
        'role': s.role, 'status': s.status,
        'created_at': s.created_at.strftime('%d %b %Y'),
    }})


@app.route('/admin/<shop>/team/import', methods=['POST'])
def team_import_staff(shop):
    if not _admin_required(shop):
        return jsonify({'success': False}), 403
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    text   = file.read().decode('utf-8', errors='ignore')
    reader = csv.DictReader(io.StringIO(text))
    created, skipped, added = 0, 0, []
    for row in reader:
        name  = (row.get('name') or row.get('Name') or '').strip()
        email = (row.get('email') or row.get('Email') or '').strip()
        role  = (row.get('role') or row.get('Role') or 'staff').strip().lower()
        if role not in ('staff', 'manager'):
            role = 'staff'
        if not name:
            skipped += 1
            continue
        s = Staff(shop=shop, name=name, email=email or None, role=role)
        db.session.add(s)
        db.session.flush()
        added.append({'id': s.id, 'name': s.name, 'email': s.email or '',
                      'role': s.role, 'status': s.status,
                      'created_at': s.created_at.strftime('%d %b %Y')})
        created += 1
    db.session.commit()
    return jsonify({'success': True, 'created': created, 'skipped': skipped, 'staff': added})


# ── Staff join link (public) ──────────────────────────────────────────────────

@app.route('/join/<token>', methods=['GET', 'POST'])
def join_link(token):
    invite = ShopInviteLink.query.filter_by(token=token).first()
    if not invite or invite.expires < datetime.utcnow():
        return render_template('join.html', error='This invite link has expired or is invalid.',
                               shop_info=None, invite=None)
    shop_info = SHOPS.get(invite.shop)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        pin  = request.form.get('pin', '').strip()
        if not name or len(pin) != 4 or not pin.isdigit():
            return render_template('join.html', shop_info=shop_info, invite=invite,
                                   error='Please enter your name and a 4-digit PIN.')
        db.session.add(Staff(
            shop=invite.shop, name=name, role='staff',
            pin_hash=generate_password_hash(pin),
            status='active', joined_at=datetime.utcnow(),
        ))
        db.session.commit()
        return render_template('join.html', shop_info=shop_info, invite=None,
                               success=True, staff_name=name)
    return render_template('join.html', shop_info=shop_info, invite=invite)


# ── SocketIO ──────────────────────────────────────────────────────────────────

@socketio.on('join_shop')
def on_join_shop(data):
    shop = data.get('shop')
    if shop in SHOPS:
        join_room(shop)
        emit('joined', {'shop': shop})


# ── Bootstrap ─────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
