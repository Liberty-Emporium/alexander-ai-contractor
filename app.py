"""
Contractor Pro AI - Multi-Tenant
All-in-one contractor app with real-time pricing + AI helper
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
import os
import json
import uuid
import base64
import hashlib
import sqlite3
from datetime import datetime
from functools import wraps
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'contractor-pro-ai-secret-2024')

DATA_DIR = os.environ.get('DATA_DIR', '/data')
os.makedirs(DATA_DIR, exist_ok=True)

PLAN_LIMITS = {
    'free':       {'bids': 5,    'products': 20},
    'pro':        {'bids': 9999, 'products': 9999},
    'enterprise': {'bids': 9999, 'products': 9999},
}

# ============== DATABASE ==============
DB_FILE = os.path.join(DATA_DIR, 'contractor_pro.db')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_FILE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            tokens_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bids (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            project_type TEXT,
            details TEXT,
            content TEXT,
            location TEXT,
            total_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT,
            category TEXT,
            price REAL,
            store TEXT,
            url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            product_name TEXT,
            price REAL,
            store TEXT,
            url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS locations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT,
            address TEXT,
            city TEXT,
            zip TEXT,
            type TEXT,
            lat TEXT,
            lon TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS user_api_keys (
            user_id TEXT PRIMARY KEY,
            qwen_key TEXT DEFAULT '',
            groq_key TEXT DEFAULT '',
            anthropic_key TEXT DEFAULT '',
            openai_key TEXT DEFAULT '',
            xai_key TEXT DEFAULT '',
            mistral_key TEXT DEFAULT '',
            active_provider TEXT DEFAULT 'qwen',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    # Default admin
    existing = db.execute("SELECT id FROM users WHERE email = 'admin'").fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, plan) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), 'admin', 'Admin', hash_password('admin1'), 'enterprise')
        )
    db.commit()
    db.close()

init_db()

# ============== ENCRYPTION ==============

def get_encryption_key():
    secret = os.environ.get('ENCRYPTION_SECRET', 'contractor-pro-ai-default-key-2024')
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)

def encrypt_value(value):
    if not value:
        return ''
    return Fernet(get_encryption_key()).encrypt(value.encode()).decode()

def decrypt_value(encrypted_value):
    if not encrypted_value:
        return ''
    try:
        return Fernet(get_encryption_key()).decrypt(encrypted_value.encode()).decode()
    except Exception:
        return ''

# ============== HELPERS ==============

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def plan_required(min_plan):
    order = ['free', 'pro', 'enterprise']
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user = get_current_user()
            if not user or order.index(user['plan']) < order.index(min_plan):
                flash(f'This feature requires the {min_plan.title()} plan. Upgrade to unlock!', 'error')
                return redirect(url_for('pricing_page'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def get_current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

def uid():
    return str(uuid.uuid4())

def get_user_api_keys(user_id):
    row = get_db().execute('SELECT * FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
    if row:
        return {
            'qwen_key': decrypt_value(row['qwen_key']),
            'groq_key': decrypt_value(row['groq_key']),
            'anthropic_key': decrypt_value(row['anthropic_key']),
            'openai_key': decrypt_value(row['openai_key']),
            'xai_key': decrypt_value(row['xai_key']),
            'mistral_key': decrypt_value(row['mistral_key']),
            'active_provider': row['active_provider'] or 'qwen'
        }
    return {'qwen_key': '', 'groq_key': '', 'anthropic_key': '', 'openai_key': '', 'xai_key': '', 'mistral_key': '', 'active_provider': 'qwen'}

def save_user_api_keys(user_id, keys, active_provider='qwen'):
    db = get_db()
    db.execute('''INSERT OR REPLACE INTO user_api_keys
        (user_id, qwen_key, groq_key, anthropic_key, openai_key, xai_key, mistral_key, active_provider, updated_at)
        VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''',
        (user_id, encrypt_value(keys.get('qwen_key', '')), encrypt_value(keys.get('groq_key', '')),
         encrypt_value(keys.get('anthropic_key', '')), encrypt_value(keys.get('openai_key', '')),
         encrypt_value(keys.get('xai_key', '')), encrypt_value(keys.get('mistral_key', '')), active_provider))
    db.commit()

# ============== AUTH ==============

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user and verify_password(password, user['password_hash']):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash('Logged in!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        name = request.form.get('name', email.split('@')[0])
        if not email or not password:
            flash('Please fill in all fields.', 'error')
            return redirect(url_for('register'))
        db = get_db()
        if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))
        user_id = uid()
        db.execute('INSERT INTO users (id, email, name, password_hash, plan) VALUES (?,?,?,?,?)',
                   (user_id, email, name, hash_password(password), 'free'))
        db.commit()
        session['user_id'] = user_id
        session['user_name'] = name
        flash('Account created! Welcome to Contractor Pro AI!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out!', 'success')
    return redirect(url_for('index'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        new_pwd = request.form.get('new_password', '')
        if new_pwd and len(new_pwd) >= 4:
            db = get_db()
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(new_pwd), session['user_id']))
            db.commit()
            flash('Password changed!', 'success')
            return redirect(url_for('dashboard'))
        flash('Password must be at least 4 characters.', 'error')
    return render_template('change_password.html')

# ============== MAIN ROUTES ==============

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    db = get_db()
    api_keys = get_user_api_keys(user_id)
    has_api_key = any([api_keys.get(k) for k in ['qwen_key', 'groq_key', 'anthropic_key', 'openai_key', 'xai_key', 'mistral_key']])

    total_products = db.execute('SELECT COUNT(*) FROM products WHERE user_id=?', (user_id,)).fetchone()[0]
    total_bids = db.execute('SELECT COUNT(*) FROM bids WHERE user_id=?', (user_id,)).fetchone()[0]
    tracked_stores = db.execute('SELECT COUNT(*) FROM locations WHERE user_id=?', (user_id,)).fetchone()[0]
    recent_bids = db.execute('SELECT * FROM bids WHERE user_id=? ORDER BY created_at DESC LIMIT 5', (user_id,)).fetchall()

    return render_template('dashboard.html',
                           total_products=total_products,
                           total_bids=total_bids,
                           tracked_stores=tracked_stores,
                           recent_bids=recent_bids,
                           has_api_key=has_api_key,
                           active_provider=api_keys.get('active_provider', 'qwen'))

# ============== BIDS ==============

@app.route('/bids')
@login_required
def list_bids():
    bids = get_db().execute('SELECT * FROM bids WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('bids.html', bids=bids)

@app.route('/ai-bid')
@login_required
def ai_bid():
    return render_template('ai_bid.html')

@app.route('/api/create-bid', methods=['POST'])
@login_required
def create_bid():
    from ai_ceo import AICEO
    user_id = session['user_id']
    api_keys = get_user_api_keys(user_id)
    ceo = AICEO(api_keys, api_keys.get('active_provider', 'qwen'))

    data = request.json
    project_type = data.get('project_type', '')
    details = data.get('details', '')
    location = data.get('location', '')

    prompt = f"""Create a contractor bid for:
Project: {project_type}
Details: {details}
Location: {location}

Include:
1. Materials list with estimated costs
2. Labor estimate
3. Timeline
4. Total estimate
5. Terms and conditions

Make it professional and detailed."""

    bid_content = ceo.think(prompt)

    db = get_db()
    bid_id = uid()
    db.execute('INSERT INTO bids (id, user_id, project_type, details, content, location) VALUES (?,?,?,?,?,?)',
               (bid_id, user_id, project_type, details, bid_content, location))
    db.commit()

    # Track token usage
    db.execute('UPDATE users SET tokens_used = tokens_used + 1 WHERE id = ?', (user_id,))
    db.commit()

    bid = db.execute('SELECT * FROM bids WHERE id = ?', (bid_id,)).fetchone()
    return jsonify({'success': True, 'bid': dict(bid)})

# ============== PRICE LOOKUP ==============

@app.route('/prices')
@login_required
def prices():
    products = get_db().execute('SELECT * FROM products WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('prices.html', products=products)

@app.route('/price-lookup', methods=['GET', 'POST'])
@login_required
def price_lookup():
    if request.method == 'POST' and request.form.get('city'):
        session['city'] = request.form.get('city', '').strip()
        session['zip'] = request.form.get('zip', '').strip()
        flash(f"Location set to {session['city']}", 'success')

    search = request.form.get('search', request.args.get('search', '')).lower()

    mock_prices = [
        {'name': '2x4x8 Lumber', 'lowes': 5.98, 'home_depot': 6.49, 'local': 5.50},
        {'name': 'Sheet of Plywood', 'lowes': 45.00, 'home_depot': 47.00, 'local': 42.00},
        {'name': 'Quikrete Concrete', 'lowes': 5.48, 'home_depot': 5.98, 'local': 5.25},
        {'name': 'Galvanized Nails 5lb', 'lowes': 12.98, 'home_depot': 14.48, 'local': 11.99},
        {'name': 'Portland Cement 94lb', 'lowes': 16.98, 'home_depot': 17.98, 'local': 15.99},
        {'name': 'PVC Pipe 10ft', 'lowes': 8.98, 'home_depot': 9.48, 'local': 8.49},
        {'name': 'Insulation R-30', 'lowes': 45.00, 'home_depot': 49.00, 'local': 42.00},
        {'name': 'Drywall 4x8', 'lowes': 15.48, 'home_depot': 16.98, 'local': 14.99},
    ]
    results = [p for p in mock_prices if search in p['name'].lower()] if search else mock_prices

    if search:
        return render_template('price_results.html', results=results, search=search)
    return render_template('price_lookup.html')

# ============== LOCATIONS ==============

@app.route('/locations')
@login_required
def locations():
    locs = get_db().execute('SELECT * FROM locations WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('locations.html', locations=locs)

@app.route('/location/add', methods=['POST'])
@login_required
def add_location():
    user_id = session['user_id']
    db = get_db()
    db.execute('INSERT INTO locations (id, user_id, name, address, city, zip, type, lat, lon) VALUES (?,?,?,?,?,?,?,?,?)',
               (uid(), user_id, request.form.get('name'), request.form.get('address'),
                request.form.get('city'), request.form.get('zip'), request.form.get('type'),
                request.form.get('lat'), request.form.get('lon')))
    db.commit()
    flash('Location added!', 'success')
    return redirect(url_for('locations'))

# ============== AI ADVISOR ==============

@app.route('/ai-advisor')
@login_required
def ai_advisor():
    return render_template('ai_advisor.html')

@app.route('/api/ask-advisor', methods=['POST'])
@login_required
def ask_advisor():
    from ai_ceo import AICEO
    user_id = session['user_id']
    api_keys = get_user_api_keys(user_id)
    ceo = AICEO(api_keys, api_keys.get('active_provider', 'qwen'))

    data = request.json
    question = data.get('question', '')
    prompt = f"""You are a construction pricing expert. Answer this contractor question:

{question}

Provide specific, actionable advice with estimated costs if applicable."""

    answer = ceo.think(prompt)
    db = get_db()
    db.execute('UPDATE users SET tokens_used = tokens_used + 1 WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({'answer': answer})

# ============== AI CEO ==============

@app.route('/ceo')
@login_required
def ceo_dashboard():
    return render_template('ceo_dashboard.html')

@app.route('/api/ceo/analyze', methods=['GET'])
@login_required
def ceo_analyze():
    from ai_ceo import AICEO
    user_id = session['user_id']
    db = get_db()
    api_keys = get_user_api_keys(user_id)
    ceo = AICEO(api_keys, api_keys.get('active_provider', 'qwen'))

    products = db.execute('SELECT COUNT(*) FROM products WHERE user_id=?', (user_id,)).fetchone()[0]
    bids = db.execute('SELECT COUNT(*) FROM bids WHERE user_id=?', (user_id,)).fetchone()[0]
    locations = db.execute('SELECT COUNT(*) FROM locations WHERE user_id=?', (user_id,)).fetchone()[0]

    prompt = f"""Analyze this contractor business:
- {products} products tracked
- {bids} bids created
- {locations} store locations tracked

Recommendations on: pricing strategies, money-making opportunities, next features."""

    analysis = ceo.think(prompt)
    return jsonify({'analysis': analysis})

# ============== SETTINGS ==============

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user_id = session['user_id']
    if request.method == 'POST':
        keys = {k: request.form.get(k, '').strip() for k in
                ['qwen_key', 'groq_key', 'anthropic_key', 'openai_key', 'xai_key', 'mistral_key']}
        active_provider = request.form.get('active_provider', 'qwen')
        save_user_api_keys(user_id, keys, active_provider)
        flash(f'API keys saved! Active provider: {active_provider}', 'success')
        return redirect(url_for('settings'))

    user = get_current_user()
    api_keys = get_user_api_keys(user_id)
    return render_template('settings.html',
                           tokens_used=user['tokens_used'],
                           is_admin=(user['plan'] == 'enterprise'),
                           active_provider=api_keys.get('active_provider', 'qwen'),
                           **{k: api_keys.get(k, '') for k in
                              ['qwen_key', 'groq_key', 'anthropic_key', 'openai_key', 'xai_key', 'mistral_key']})

# ============== BILLING ==============

STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
stripe_enabled = bool(STRIPE_SECRET_KEY and STRIPE_SECRET_KEY.startswith('sk_'))

if stripe_enabled:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

@app.route('/pricing')
def pricing_page():
    user = get_current_user()
    return render_template('index.html', current_plan=user['plan'] if user else 'free')

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    if not stripe_enabled:
        return jsonify({'error': 'Stripe not configured'}), 400
    user = get_current_user()
    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            success_url=request.host_url + 'dashboard?upgraded=1',
            cancel_url=request.host_url + 'pricing',
            metadata={'user_id': user['id']}
        )
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    if not stripe_enabled:
        return jsonify({'error': 'Stripe not configured'}), 400
    payload = request.data
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            obj = event['data']['object']
            user_id = obj.get('metadata', {}).get('user_id')
            customer_id = obj.get('customer')
            if user_id:
                db = get_db()
                db.execute("UPDATE users SET plan='pro', stripe_customer_id=? WHERE id=?", (customer_id, user_id))
                db.commit()
        elif event['type'] == 'customer.subscription.deleted':
            customer_id = event['data']['object'].get('customer')
            if customer_id:
                db = get_db()
                db.execute("UPDATE users SET plan='free' WHERE stripe_customer_id=?", (customer_id,))
                db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ============== MISC ==============

@app.route('/new-products')
@login_required
def new_products():
    return render_template('new_products.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
