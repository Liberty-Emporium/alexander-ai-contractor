"""
Contractor Pro AI
All-in-one contractor app with real-time pricing + AI helper
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.urandom(24)

DATA_DIR = os.environ.get('DATA_DIR', os.path.join('/data'))
os.makedirs(DATA_DIR, exist_ok=True)

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
@app.route('/dashboard')
def dashboard():
    products = load_products()
    bids = load_bids()
    
    # Check for API keys - always read fresh from environment
    groq_key = os.environ.get('GROQ_API_KEY', '') or os.getenv('GROQ_API_KEY', '')
    qwen_key = os.environ.get('QWEN_API_KEY', '') or os.getenv('QWEN_API_KEY', '')
    locations = load_locations()
    
    # Stats
    total_products = len(products)
    total_bids = len(bids)
    tracked_stores = len(locations)
    
    return render_template('dashboard.html',
                          total_products=total_products,
                          total_bids=total_bids,
                          tracked_stores=tracked_stores,
                          recent_bids=bids[-5:] if bids else [])

# ============== PRICE LOOKUP ==============

@app.route('/prices')
def prices():
    """Price comparison page"""
    products = load_products()
    return render_template('prices.html', products=products)

@app.route('/price-lookup', methods=['GET', 'POST'])
def price_lookup():
    # Handle location save
    if request.method == 'POST' and request.form.get('city'):
        city = request.form.get('city', '').strip()
        zip_code = request.form.get('zip', '').strip()
        if city:
            session['city'] = city
            session['zip'] = zip_code
            flash(f'Location set to {city}', 'success')
    
    # Handle both POST and GET
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
    """AI-powered bid creation"""
    return render_template('ai_bid.html')

@app.route('/api/create-bid', methods=['POST'])
def create_bid():
    """Generate AI bid"""
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
    
    # Save bid
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
    """List all bids"""
    bids = load_bids()
    return render_template('bids.html', bids=bids)

# ============== AI ADVISOR ==============

@app.route('/ai-advisor')
def ai_advisor():
    """AI pricing advisor"""
    return render_template('ai_advisor.html')

@app.route('/api/ask-advisor', methods=['POST'])
def ask_advisor():
    """Ask AI for pricing advice"""
    from ai_ceo import ceo
    
    data = request.json
    question = data.get('question', '')
    
    prompt = f"""You are a construction pricing expert. Answer this contractor question:

{question}

Provide specific, actionable advice with estimated costs if applicable."""
    
    answer = ceo.think(prompt)
    
    return jsonify({'answer': answer})

# ============== LOCATIONS (GPS) ==============

@app.route('/locations')
def locations():
    """Manage store locations"""
    locations = load_locations()
    return render_template('locations.html', locations=locations)

@app.route('/location/add', methods=['POST'])
def add_location():
    """Add a store location"""
    locations = load_locations()
    
    location = {
        'id': len(locations) + 1,
        'name': request.form.get('name'),
        'address': request.form.get('address'),
        'city': request.form.get('city'),
        'zip': request.form.get('zip'),
        'type': request.form.get('type'),  # lowes, home_depot, local
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
    """Track new products"""
    return render_template('new_products.html')

# ============== AI CEO ==============

@app.route('/ceo')
def ceo_dashboard():
    """AI CEO for Contractor business"""
    return render_template('ceo_dashboard.html')

@app.route('/api/ceo/analyze', methods=['GET'])
def ceo_analyze():
    """Get AI analysis"""
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

# ============== STATIC ==============

@app.route('/pricing')
def pricing_page():
    return render_template('pricing.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)


@app.route('/settings')
def settings():
    """API Key Settings"""
    groq_key = os.environ.get('GROQ_API_KEY', '')
    qwen_key = os.environ.get('QWEN_API_KEY', '')
    return render_template('settings.html', groq_key=groq_key, qwen_key=qwen_key)

@app.route('/settings', methods=['POST'])
def settings_save():
    """Save API Keys"""
    groq = request.form.get('groq_key', '').strip()
    qwen = request.form.get('qwen_key', '').strip()
    
    if groq:
        os.environ['GROQ_API_KEY'] = groq
    if qwen:
        os.environ['QWEN_API_KEY'] = qwen
    
    flash('API keys updated!', 'success')
    return redirect(url_for('dashboard'))
