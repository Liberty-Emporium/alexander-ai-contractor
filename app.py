"""
Contractor Pro AI
All-in-one contractor app with real-time pricing + AI helper
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import json
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.urandom(24)

DATA_DIR = os.environ.get('DATA_DIR', os.path.join('/data'))
os.makedirs(DATA_DIR, exist_ok=True)

# ============== DATABASE SETUP ==============
db_path = os.path.join(DATA_DIR, 'contractor_pro.db')
conn = sqlite3.connect(db_path, check_same_thread=False)
c = conn.cursor()

# Create tables
c.execute('''CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    project_type TEXT,
    materials TEXT,
    labor_hours REAL,
    labor_rate REAL,
    profit_margin REAL,
    total_price REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

c.execute('''CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    product_name TEXT,
    price REAL,
    store TEXT,
    url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

c.execute('''CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    name TEXT,
    category TEXT,
    price REAL,
    store TEXT,
    url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# User API keys - STORED IN DATABASE, NOT ENV!
c.execute('''CREATE TABLE IF NOT EXISTS user_api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE,
    qwen_key TEXT,
    groq_key TEXT,
    anthropic_key TEXT,
    openai_key TEXT,
    xai_key TEXT,
    mistral_key TEXT,
    active_provider TEXT DEFAULT 'qwen',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

conn.commit()

# ============== ENCRYPTION ==============
def get_encryption_key():
    secret = os.environ.get('ENCRYPTION_SECRET', 'contractor-pro-ai-default-key-2024')
    salt = b'contractor-pro-salt'
    kdf = PBKDF2(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))

def encrypt_value(value):
    if not value:
        return ''
    f = Fernet(get_encryption_key())
    return f.encrypt(value.encode()).decode()

def decrypt_value(encrypted_value):
    if not encrypted_value:
        return ''
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(encrypted_value.encode()).decode()
    except:
        return ''

# ============== HELPER FUNCTIONS ==============

def get_user_api_keys(user_id):
    """Get user's API keys from database"""
    c.execute('SELECT qwen_key, groq_key, anthropic_key, openai_key, xai_key, mistral_key, active_provider FROM user_api_keys WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    if row:
        return {
            'qwen_key': decrypt_value(row[0]),
            'groq_key': decrypt_value(row[1]),
            'anthropic_key': decrypt_value(row[2]),
            'openai_key': decrypt_value(row[3]),
            'xai_key': decrypt_value(row[4]),
            'mistral_key': decrypt_value(row[5]),
            'active_provider': row[6] or 'qwen'
        }
    return {'qwen_key': '', 'groq_key': '', 'anthropic_key': '', 'openai_key': '', 'xai_key': '', 'mistral_key': '', 'active_provider': 'qwen'}

def save_user_api_keys(user_id, keys, active_provider='qwen'):
    """Save user's API keys to database"""
    c.execute('''INSERT OR REPLACE INTO user_api_keys 
        (user_id, qwen_key, groq_key, anthropic_key, openai_key, xai_key, mistral_key, active_provider, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
        (user_id, encrypt_value(keys.get('qwen_key', '')), encrypt_value(keys.get('groq_key', '')), encrypt_value(keys.get('anthropic_key', '')),
         encrypt_value(keys.get('openai_key', '')), encrypt_value(keys.get('xai_key', '')), encrypt_value(keys.get('mistral_key', '')), active_provider))
    conn.commit()

def get_active_ai_key(user_id):
    """Get the active AI provider's key for this user"""
    api_keys = get_user_api_keys(user_id)
    provider = api_keys.get('active_provider', 'qwen')
    
    key_map = {
        'qwen': api_keys.get('qwen_key', ''),
        'groq': api_keys.get('groq_key', ''),
        'anthropic': api_keys.get('anthropic_key', ''),
        'openai': api_keys.get('openai_key', ''),
        'xai': api_keys.get('xai_key', ''),
        'mistral': api_keys.get('mistral_key', ''),
    }
    
    return key_map.get(provider, ''), provider

# ============== DATA FUNCTIONS ==============

def load_products():
    path = os.path.join(DATA_DIR, 'products.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_products(products):
    with open(os.path.join(DATA_DIR, 'products.json'), 'w') as f:
        json.dump(products, f, indent=2)

def load_bids():
    path = os.path.join(DATA_DIR, 'bids.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_bids(bids):
    with open(os.path.join(DATA_DIR, 'bids.json'), 'w') as f:
        json.dump(bids, f, indent=2)

def load_locations():
    path = os.path.join(DATA_DIR, 'locations.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_locations(locations):
    with open(os.path.join(DATA_DIR, 'locations.json'), 'w') as f:
        json.dump(locations, f, indent=2)

# ============== ROUTES ==============

@app.route('/')
def index():
    """Landing page"""
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    products = load_products()
    bids = load_bids()
    locations = load_locations()
    
    # Get user's API key status from database
    user_id = session.get('user_id', 'guest')
    api_keys = get_user_api_keys(user_id)
    has_api_key = any([api_keys.get('qwen_key'), api_keys.get('groq_key'), 
                      api_keys.get('anthropic_key'), api_keys.get('openai_key'),
                      api_keys.get('xai_key'), api_keys.get('mistral_key')])
    
    total_products = len(products)
    total_bids = len(bids)
    tracked_stores = len(locations)
    
    return render_template('dashboard.html',
                          total_products=total_products,
                          total_bids=total_bids,
                          tracked_stores=tracked_stores,
                          recent_bids=bids[-5:] if bids else [],
                          has_api_key=has_api_key,
                          active_provider=api_keys.get('active_provider', 'qwen'))

# ============== PRICE LOOKUP ==============

@app.route('/prices')
def prices():
    products = load_products()
    return render_template('prices.html', products=products)

@app.route('/price-lookup', methods=['GET', 'POST'])
def price_lookup():
    if request.method == 'POST' and request.form.get('city'):
        city = request.form.get('city', '').strip()
        zip_code = request.form.get('zip', '').strip()
        if city:
            session['city'] = city
            session['zip'] = zip_code
            flash(f'Location set to {city}', 'success')
    
    search = ''
    if request.method == 'POST' and request.form.get('search'):
        search = request.form.get('search', '').lower()
    else:
        search = request.args.get('search', '').lower()
    
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

# ============== AI BID HELPER ==============

@app.route('/ai-bid')
def ai_bid():
    return render_template('ai_bid.html')

@app.route('/api/create-bid', methods=['POST'])
def create_bid():
    from ai_ceo import ceo
    
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
    
    bids = load_bids()
    bid = {
        'id': f"BID-{len(bids) + 1:04d}",
        'project_type': project_type,
        'details': details,
        'content': bid_content,
        'created_at': datetime.now().isoformat()
    }
    bids.append(bid)
    save_bids(bids)
    
    return jsonify({'success': True, 'bid': bid})

@app.route('/bids')
def list_bids():
    bids = load_bids()
    return render_template('bids.html', bids=bids)

# ============== AI ADVISOR ==============

@app.route('/ai-advisor')
def ai_advisor():
    return render_template('ai_advisor.html')

@app.route('/api/ask-advisor', methods=['POST'])
def ask_advisor():
    from ai_ceo import ceo
    
    data = request.json
    question = data.get('question', '')
    
    prompt = f"""You are a construction pricing expert. Answer this contractor question:

{question}

Provide specific, actionable advice with estimated costs if applicable."""
    
    answer = ceo.think(prompt)
    
    return jsonify({'answer': answer})

# ============== LOCATIONS ==============

@app.route('/locations')
def locations():
    locations = load_locations()
    return render_template('locations.html', locations=locations)

@app.route('/location/add', methods=['POST'])
def add_location():
    locations = load_locations()
    
    location = {
        'id': len(locations) + 1,
        'name': request.form.get('name'),
        'address': request.form.get('address'),
        'city': request.form.get('city'),
        'zip': request.form.get('zip'),
        'type': request.form.get('type'),
        'lat': request.form.get('lat'),
        'lon': request.form.get('lon')
    }
    
    locations.append(location)
    save_locations(locations)
    flash('Location added!', 'success')
    return redirect(url_for('locations'))

# ============== NEW PRODUCTS ==============

@app.route('/new-products')
def new_products():
    return render_template('new_products.html')

# ============== AI CEO ==============

@app.route('/ceo')
def ceo_dashboard():
    return render_template('ceo_dashboard.html')

@app.route('/api/ceo/analyze', methods=['GET'])
def ceo_analyze():
    from ai_ceo import ceo
    
    products = load_products()
    bids = load_bids()
    locations = load_locations()
    
    prompt = f"""Analyze this contractor business data:
- {len(products)} products tracked
- {len(bids)} bids created
- {len(locations)} store locations

Give recommendations on:
1. Best money-making opportunities
2. Pricing strategies
3. What features to add next"""
    
    analysis = ceo.think(prompt)
    
    return jsonify({'analysis': analysis})

# ============== SETTINGS ==============

@app.route('/settings')
def settings():
    tokens_used = session.get('tokens_used', 0)
    is_admin = session.get('username') == 'admin'
    user_id = session.get('user_id', 'guest')
    
    # Get user's API keys from database
    api_keys = get_user_api_keys(user_id)
    
    return render_template('settings.html', 
                         tokens_used=tokens_used, 
                         is_admin=is_admin, 
                         qwen_key=api_keys.get('qwen_key', ''), 
                         groq_key=api_keys.get('groq_key', ''), 
                         anthropic_key=api_keys.get('anthropic_key', ''), 
                         openai_key=api_keys.get('openai_key', ''),
                         xai_key=api_keys.get('xai_key', ''), 
                         mistral_key=api_keys.get('mistral_key', ''),
                         active_provider=api_keys.get('active_provider', 'qwen'))

@app.route('/settings', methods=['POST'])
def settings_save():
    user_id = session.get('user_id', 'guest')
    active_provider = request.form.get('active_provider', 'qwen')
    
    keys = {
        'qwen_key': request.form.get('qwen_key', '').strip(),
        'groq_key': request.form.get('groq_key', '').strip(),
        'anthropic_key': request.form.get('anthropic_key', '').strip(),
        'openai_key': request.form.get('openai_key', '').strip(),
        'xai_key': request.form.get('xai_key', '').strip(),
        'mistral_key': request.form.get('mistral_key', '').strip(),
    }
    
    save_user_api_keys(user_id, keys, active_provider)
    flash(f'API keys saved! Active provider: {active_provider}', 'success')
    
    return redirect(url_for('settings'))

# ============== PASSWORD & LOGOUT ==============

@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if request.method == 'POST':
        old_pwd = request.form.get('old_password', '')
        new_pwd = request.form.get('new_password', '')
        
        if new_pwd and len(new_pwd) >= 4:
            flash('Password changed successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Password must be at least 4 characters', 'error')
    
    return render_template('change_password.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect('/')

# ============== STATIC ==============

@app.route('/pricing')
def pricing_page():
    return render_template('pricing.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)