"""
Contractor Pro AI — Multi-Tenant SaaS
AI-powered bidding & project management for contractors
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g

import time as _rl_time
from collections import defaultdict as _defaultdict
_rate_store = _defaultdict(list)
_RATE_WINDOW = 60
_RATE_MAX = 10

def _check_login_rate(ip):
    now = _rl_time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_MAX:
        return False
    _rate_store[ip].append(now)
    return True

import os, json, sqlite3, hashlib, secrets, datetime, functools, re

# ============================================================
# RATE LIMITER — No external dependencies required
# ============================================================
import time as _rl_time

def _is_rate_limited(db, key, max_calls=5, window_seconds=60):
    """Returns True if this key has exceeded the rate limit."""
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT NOT NULL, window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (key, window_start))""")
        db.execute("DELETE FROM rate_limits WHERE window_start < ?",
                   (int(_rl_time.time()) - window_seconds * 2,))
        now = int(_rl_time.time())
        ws = now - (now % window_seconds)
        row = db.execute(
            "SELECT count FROM rate_limits WHERE key=? AND window_start=?",
            (key, ws)).fetchone()
        if row is None:
            db.execute("INSERT OR IGNORE INTO rate_limits VALUES (?,?,1)", (key, ws))
            db.commit()
            return False
        if row[0] >= max_calls:
            return True
        db.execute("UPDATE rate_limits SET count=count+1 WHERE key=? AND window_start=?",
                   (key, ws))
        db.commit()
        return False
    except Exception:
        return False


app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TENANT INFRASTRUCTURE — Applied 2026-04-16
# ══════════════════════════════════════════════════════════════════════════════
import re as _re, threading as _threading, queue as _queue, zipfile as _zipfile
import io as _io
from functools import wraps as _wraps

# ── 1. Slug Validation ────────────────────────────────────────────────────────
def _validate_slug(slug):
    """Sanitize and validate a tenant slug. Raises ValueError if invalid."""
    if not slug:
        raise ValueError("Empty slug")
    clean = _re.sub(r"[^a-z0-9\-]", "", str(slug).lower().strip())
    clean = _re.sub(r"-+", "-", clean).strip("-")[:60]
    reserved = {"admin","api","static","health","login","logout","overseer","guest","demo"}
    if not clean or clean in reserved:
        raise ValueError(f"Invalid or reserved slug: {slug}")
    return clean

# ── 2. Audit Log ──────────────────────────────────────────────────────────────
_AUDIT_FILE = os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "audit.log"
)

def _audit(action, slug=None, user=None, details=None):
    """Fire-and-forget audit entry. Never raises."""
    try:
        from datetime import datetime as _dt
        import json as _j
        slug  = slug  or (session.get("impersonating_slug") or session.get("store_slug") or "system")
        user  = user  or session.get("username", "unknown")
        ip    = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr) if request else ""
        line  = _j.dumps({
            "ts": _dt.utcnow().isoformat(),
            "slug": slug, "user": user,
            "action": action, "ip": ip,
            "details": details or {}
        })
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        with open(_AUDIT_FILE, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        pass

# ── 3. Background Job Queue ───────────────────────────────────────────────────
class _JobQueue:
    def __init__(self):
        self._q = _queue.Queue()
        t = _threading.Thread(target=self._worker, daemon=True)
        t.start()
    def enqueue(self, fn, *args, **kwargs):
        self._q.put((fn, args, kwargs))
    def _worker(self):
        while True:
            try:
                fn, args, kwargs = self._q.get(timeout=1)
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    try:
                        app.logger.error(f"[JobQueue] {e}")
                    except Exception:
                        pass
                self._q.task_done()
            except _queue.Empty:
                pass

_job_queue = _JobQueue()

# ── 4. Per-Tenant Rate Limiter ────────────────────────────────────────────────
import time as _time
from collections import defaultdict as _defaultdict
_tenant_calls = _defaultdict(list)

def _tenant_rate_ok(slug, max_calls=120, window=60):
    now = _time.time()
    _tenant_calls[slug] = [t for t in _tenant_calls[slug] if now - t < window]
    if len(_tenant_calls[slug]) >= max_calls:
        return False
    _tenant_calls[slug].append(now)
    return True

def _tenant_rate_limit(max_calls=120):
    def decorator(f):
        @_wraps(f)
        def decorated(*args, **kwargs):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and not _tenant_rate_ok(slug, max_calls):
                return jsonify({"error": "Too many requests. Please slow down."}), 429
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── 5. Trial Status ───────────────────────────────────────────────────────────
def _get_trial_status(slug):
    """Returns 'paid', 'active', or 'expired'."""
    try:
        from datetime import datetime as _dt
        cfg_path = os.path.join(
            os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"),
            "customers", slug, "config.json"
        )
        if not os.path.exists(cfg_path):
            return "active"
        with open(cfg_path) as f:
            import json as _j; cfg = _j.load(f)
        if cfg.get("plan") == "paid":
            return "paid"
        trial_end = cfg.get("trial_ends")
        if not trial_end:
            return "active"
        return "active" if _dt.utcnow() < _dt.fromisoformat(trial_end) else "expired"
    except Exception:
        return "active"

def _trial_gate(f):
    """Redirect expired trials to upgrade page."""
    @_wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_guest"):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and _get_trial_status(slug) == "expired":
                if not session.get("role") == "overseer":
                    flash("Your trial has expired. Upgrade to continue.", "warning")
                    return redirect("/upgrade")
        return f(*args, **kwargs)
    return decorated

# ── 6. Tenant Health Summary (for Overseer) ────────────────────────────────────
def _get_tenant_health():
    from datetime import datetime as _dt
    import json as _j, csv as _csv
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    stores = []
    if not os.path.exists(customers_dir):
        return stores
    for slug in os.listdir(customers_dir):
        cfg_path = os.path.join(customers_dir, slug, "config.json")
        if not os.path.isdir(os.path.join(customers_dir, slug)):
            continue
        if not os.path.exists(cfg_path):
            continue
        try:
            with open(cfg_path) as f:
                cfg = _j.load(f)
            status = _get_trial_status(slug)
            trial_end = cfg.get("trial_ends","")
            days_left = 0
            if status == "active" and trial_end:
                days_left = max(0, (_dt.fromisoformat(trial_end) - _dt.utcnow()).days)

            # Count items
            items = 0
            inv = os.path.join(customers_dir, slug, "inventory.csv")
            if os.path.exists(inv):
                with open(inv) as f:
                    items = max(0, sum(1 for _ in f) - 1)

            # Last active
            tdir = os.path.join(customers_dir, slug)
            mtimes = [os.path.getmtime(os.path.join(tdir, fn))
                      for fn in os.listdir(tdir)
                      if os.path.isfile(os.path.join(tdir, fn))]
            last_active = _dt.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M") if mtimes else ""

            stores.append({
                "slug":        slug,
                "store_name":  cfg.get("store_name", slug),
                "email":       cfg.get("contact_email",""),
                "plan":        cfg.get("plan","trial"),
                "status":      status,
                "days_left":   days_left,
                "items":       items,
                "created":     cfg.get("created_at","")[:10],
                "last_active": last_active,
                "mrr":         20.0 if cfg.get("plan") == "paid" else 0,
            })
        except Exception:
            continue
    return sorted(stores, key=lambda x: (x["plan"] != "paid", x["last_active"]), reverse=True)

# ── 7. Data Export ─────────────────────────────────────────────────────────────
@app.route("/settings/export-data")
def _export_tenant_data():
    if not session.get("logged_in"):
        return redirect("/login")
    if session.get("is_guest"):
        flash("Sign up to export your data.", "error")
        return redirect("/")
    slug = session.get("impersonating_slug") or session.get("store_slug")
    if not slug:
        abort(403)
    _audit("data_export", slug)
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    tenant_dir = os.path.join(customers_dir, slug)
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(tenant_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, tenant_dir)
                zf.write(full, arcname)
    buf.seek(0)
    safe_slug = _re.sub(r"[^a-z0-9\-]", "", slug)
    from flask import send_file as _sf
    return _sf(buf, mimetype="application/zip", as_attachment=True,
               download_name=f"{safe_slug}-data-export.zip")

# ── 8. Overseer Tenant Health API ────────────────────────────────────────────
@app.route("/overseer/tenant-health")
def _overseer_tenant_health():
    if session.get("role") != "overseer" and session.get("username") != "admin":
        abort(403)
    return jsonify(_get_tenant_health())

# ══════════════════════════════════════════════════════════════════════════════
# END MULTI-TENANT INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

# Session security hardening
app.config['SESSION_COOKIE_SECURE'] = False  # Set True when HTTPS confirmed
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour
app.secret_key = os.environ.get('SECRET_KEY', 'contractor-pro-secret-2026')

import secrets as _secrets_module

def _get_csrf_token():
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets_module.token_hex(32)
    return session['csrf_token']

def _validate_csrf():
    """Validate CSRF token on POST requests. Returns True if valid."""
    if request.method != 'POST':
        return True
    # Skip API routes
    if request.path.startswith('/api/'):
        return True
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return token and token == session.get('csrf_token')

app.jinja_env.globals['csrf_token'] = _get_csrf_token


# ── Data dirs ──────────────────────────────────────────────────────────────────
_data_pref = os.environ.get('DATA_DIR', '/data')
try:
    os.makedirs(_data_pref, exist_ok=True)
    _t = os.path.join(_data_pref, '.write_test')
    open(_t,'w').close(); os.remove(_t)
    DATA_DIR = _data_pref
except Exception:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(DATA_DIR, exist_ok=True)

CUSTOMERS_DIR = os.path.join(DATA_DIR, 'customers')
os.makedirs(CUSTOMERS_DIR, exist_ok=True)

ADMIN_USER  = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS  = os.environ.get('ADMIN_PASSWORD', 'admin1')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'jay@libertyemporium.com')
APP_NAME    = 'Contractor Pro AI'

# ── DB ─────────────────────────────────────────────────────────────────────────
DB_FILE = os.path.join(DATA_DIR, 'contractor.db')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_FILE)
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        email TEXT,
        store_slug TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY, value TEXT
    )''')
    pw = hashlib.sha256(ADMIN_PASS.encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO users (username,password,role,email) VALUES (?,?,?,?)',
               (ADMIN_USER, pw, 'admin', ADMIN_EMAIL))
    db.commit(); db.close()

init_db()

# ── Helpers ────────────────────────────────────────────────────────────────────
def hash_pw(pw): return _bcrypt_hash(pw)
def slugify(name): return re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')[:40]

def load_json(path, default=None):
    if default is None: default = []
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w') as f: json.dump(data, f, indent=2)

# ── Tenant helpers ─────────────────────────────────────────────────────────────
def active_slug():
    return session.get('impersonating_slug') or session.get('store_slug') or None

def tenant_dir(slug):
    d = os.path.join(CUSTOMERS_DIR, slug)
    os.makedirs(d, exist_ok=True)
    return d

def data_path(filename, slug=None):
    if slug: return os.path.join(tenant_dir(slug), filename)
    return os.path.join(DATA_DIR, filename)

def load_client_config(slug):
    return load_json(os.path.join(CUSTOMERS_DIR, slug, 'config.json'), {})

def save_client_config(slug, cfg):
    os.makedirs(os.path.join(CUSTOMERS_DIR, slug), exist_ok=True)
    save_json(os.path.join(CUSTOMERS_DIR, slug, 'config.json'), cfg)

def list_client_stores():
    stores = []
    if not os.path.exists(CUSTOMERS_DIR): return stores
    for slug in os.listdir(CUSTOMERS_DIR):
        cfg_path = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f: cfg = json.load(f)
                stores.append(cfg)
            except: pass
    return sorted(stores, key=lambda s: s.get('created_at',''), reverse=True)

def load_leads():  return load_json(os.path.join(DATA_DIR,'leads.json'))
def save_leads(d): save_json(os.path.join(DATA_DIR,'leads.json'), d)

# Tenant data loaders
def load_bids(slug=None):       return load_json(data_path('bids.json', slug))
def save_bids(d, slug=None):    save_json(data_path('bids.json', slug), d)
def load_products(slug=None):   return load_json(data_path('products.json', slug))
def save_products(d, slug=None):save_json(data_path('products.json', slug), d)
def load_locations(slug=None):  return load_json(data_path('locations.json', slug))
def save_locations(d, slug=None):save_json(data_path('locations.json', slug), d)

def get_config(key, default=''):
    db = get_db()
    row = db.execute('SELECT value FROM app_config WHERE key=?',(key,)).fetchone()
    return row['value'] if row else default

def set_config(key, value):
    get_db().execute('INSERT OR REPLACE INTO app_config (key,value) VALUES (?,?)',(key,str(value)))
    get_db().commit()

# ── AI ─────────────────────────────────────────────────────────────────────────
def get_ai_key(slug=None):
    if slug:
        cfg = load_client_config(slug)
        if cfg.get('openrouter_key'): return cfg['openrouter_key']
    return get_config('openrouter_key', os.environ.get('OPENROUTER_API_KEY',''))

def get_ai_model(slug=None):
    if slug:
        cfg = load_client_config(slug)
        if cfg.get('openrouter_model'): return cfg['openrouter_model']
    return get_config('openrouter_model','openai/gpt-4o-mini')

def ai_chat(messages, slug=None):
    import urllib.request as ur
    key = get_ai_key(slug)
    if not key: return "AI unavailable — add your OpenRouter API key in Settings ⚙️"
    try:
        payload = json.dumps({'model':get_ai_model(slug),'messages':messages,'max_tokens':1000}).encode()
        req = ur.Request('https://openrouter.ai/api/v1/chat/completions', data=payload, headers={
            'Authorization':f'Bearer {key}','Content-Type':'application/json',
            'HTTP-Referer':'https://libertyemporium.com','X-Title':APP_NAME})
        with ur.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())['choices'][0]['message']['content']
    except Exception as e:
        return f"AI error: {e}"

# ── Auth decorators ────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*a, **kw):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*a, **kw)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*a, **kw):
        if not session.get('logged_in') or session.get('role') != 'admin':
            flash('Admin access required.','error')
            return redirect(url_for('login'))
        return f(*a, **kw)
    return decorated

# ── Context ────────────────────────────────────────────────────────────────────
def ctx():
    slug = active_slug()
    store_name = APP_NAME
    if slug:
        cfg = load_client_config(slug)
        store_name = cfg.get('store_name', APP_NAME)
    return {
        'app_name': APP_NAME,
        'store_name': store_name,
        'current_user': session.get('username'),
        'current_role': session.get('role'),
        'store_slug': slug,
        'impersonating': bool(session.get('impersonating_slug')),
    }

# ── Public / Landing ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    if session.get('logged_in'): return redirect(url_for('dashboard'))
    return render_template('landing.html', **ctx())

@app.route('/healthz')
def healthz(): return 'ok'

@app.route('/health')
def health_check():
    """Health check endpoint."""
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception as e:
        db_status = f"error"
    import json
    status = "ok" if db_status == "ok" else "degraded"
    return json.dumps({"status": status, "db": db_status}),            200 if status == "ok" else 503,            {"Content-Type": "application/json"}



# ── Auth ───────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
    # Rate limiting — 10 login attempts per minute per IP
    _ip = request.remote_addr or 'unknown'
    if _is_rate_limited(get_db(), f'login:{_ip}', max_calls=10, window_seconds=60):
        return jsonify({'error': 'Too many login attempts. Please wait 1 minute.'}), 429

    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if user and user['password'] == hash_pw(password):
            session.clear()
            session['logged_in'] = True
            session['username']  = username
            session['role']      = user['role']
            if user['store_slug']: session['store_slug'] = user['store_slug']
            return redirect(url_for('dashboard'))
        # Also check per-tenant users
        for store in list_client_stores():
            users_path = os.path.join(CUSTOMERS_DIR, store['slug'], 'users.json')
            users = load_json(users_path, {})
            u = users.get(username)
            if u and u.get('password') == hash_pw(password):
                session.clear()
                session['logged_in']  = True
                session['username']   = username
                session['role']       = u.get('role','client')
                session['store_slug'] = store['slug']
                return redirect(url_for('dashboard'))
        flash('Invalid credentials.','error')
    return render_template('login.html', **ctx())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ── Trial signup ───────────────────────────────────────────────────────────────
@app.route('/wizard')
def wizard():
    return render_template('wizard.html', **ctx())

@app.route('/start-trial', methods=['POST'])
def start_trial():
    store_name    = request.form.get('store_name','').strip()
    contact_email = request.form.get('contact_email','').strip()
    contact_name  = request.form.get('contact_name','').strip()
    specialty     = request.form.get('specialty','general').strip()
    if not store_name or not contact_email:
        flash('Business name and email are required.','error')
        return redirect(url_for('wizard'))
    # Block duplicate email
    for store in list_client_stores():
        users_path = os.path.join(CUSTOMERS_DIR, store['slug'], 'users.json')
        users = load_json(users_path, {})
        if contact_email in users:
            flash(f'Account with {contact_email} already exists. Sign in instead.','error')
            return redirect(url_for('login'))
    slug = slugify(store_name)
    base = slug; counter = 1
    while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
        slug = f'{base}-{counter}'; counter += 1
    now = datetime.datetime.now().isoformat()
    trial_end = (datetime.datetime.now() + datetime.timedelta(days=14)).isoformat()
    cfg = {'store_name':store_name,'slug':slug,'contact_name':contact_name,
           'contact_email':contact_email,'specialty':specialty,
           'plan':'trial','status':'active',
           'trial_start':now,'trial_end':trial_end,'created_at':now}
    save_client_config(slug, cfg)
    temp_pw = secrets.token_urlsafe(8)
    save_json(os.path.join(CUSTOMERS_DIR, slug, 'users.json'),
              {contact_email: {'password':hash_pw(temp_pw),'role':'client','store_slug':slug,'created_at':now}})
    leads = load_leads()
    leads.append({'store_name':store_name,'contact_email':contact_email,'slug':slug,'created_at':now,'type':'trial'})
    save_leads(leads)
    session.clear()
    session.update({'logged_in':True,'username':contact_email,'role':'client','store_slug':slug})
    flash(f'Welcome! Your login: {contact_email} / {temp_pw} — save this!','success')
    return redirect(url_for('dashboard'))

# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    slug  = active_slug()
    bids  = load_bids(slug)
    prods = load_products(slug)
    locs  = load_locations(slug)
    total_value = sum(float(b.get('total_price',0)) for b in bids)
    return render_template('dashboard.html',
        total_bids=len(bids), total_products=len(prods),
        tracked_stores=len(locs), total_value=total_value,
        recent_bids=bids[-5:] if bids else [],
        has_api_key=bool(get_ai_key(slug)),
        **ctx())

# ── Bids ───────────────────────────────────────────────────────────────────────
@app.route('/bids')
@login_required
def list_bids():
    return render_template('bids.html', bids=load_bids(active_slug()), **ctx())

@app.route('/ai-bid')
@login_required
def ai_bid():
    return render_template('ai_bid.html', locations=load_locations(active_slug()), **ctx())

@app.route('/api/create-bid', methods=['POST'])
@login_required
def create_bid():
    slug = active_slug()
    data = request.get_json() or {}
    project_type   = data.get('project_type','')
    location       = data.get('location','')
    description    = data.get('description','')
    materials_list = data.get('materials',[])
    labor_hours    = float(data.get('labor_hours',0))
    labor_rate     = float(data.get('labor_rate',65))
    profit_margin  = float(data.get('profit_margin',20))

    system = (f"You are an expert contractor estimator. Create a detailed, professional bid. "
              f"Be specific with costs. Location: {location}.")
    prompt = (f"Project: {project_type}\nDescription: {description}\n"
              f"Materials needed: {', '.join(materials_list)}\n"
              f"Labor: {labor_hours}hrs @ ${labor_rate}/hr\nProfit margin: {profit_margin}%\n"
              f"Provide: itemized material costs, labor cost, subtotal, profit, and final price.")
    ai_response = ai_chat([{'role':'system','content':system},{'role':'user','content':prompt}], slug)

    materials_cost = sum(50 * len(materials_list), 0) if materials_list else 200
    labor_cost     = labor_hours * labor_rate
    subtotal       = materials_cost + labor_cost
    total_price    = subtotal * (1 + profit_margin/100)

    bid = {
        'id': f"BID-{len(load_bids(slug))+1:04d}",
        'project_type': project_type, 'location': location,
        'description': description, 'materials': materials_list,
        'labor_hours': labor_hours, 'labor_rate': labor_rate,
        'profit_margin': profit_margin, 'materials_cost': round(materials_cost,2),
        'labor_cost': round(labor_cost,2), 'total_price': round(total_price,2),
        'ai_breakdown': ai_response,
        'created_at': datetime.datetime.now().isoformat()
    }
    bids = load_bids(slug)
    bids.append(bid)
    save_bids(bids, slug)
    return jsonify({'success':True,'bid':bid})

# ── Products / Price tracking ──────────────────────────────────────────────────
@app.route('/prices')
@login_required
def prices():
    return render_template('prices.html', products=load_products(active_slug()), **ctx())

@app.route('/price-lookup', methods=['GET','POST'])
@login_required
def price_lookup():
    slug = active_slug()
    if request.method == 'POST' and request.form.get('city'):
        city     = request.form.get('city','')
        material = request.form.get('material','lumber')
        prompt   = (f"Current {material} prices in {city}. List 5 specific products with realistic "
                    f"prices from Home Depot, Lowe's, or local suppliers. Include product name, "
                    f"unit, price, and store.")
        result = ai_chat([{'role':'user','content':prompt}], slug)
        return render_template('price_results.html', result=result,
                               city=city, material=material, **ctx())
    return render_template('price_lookup.html', **ctx())

@app.route('/new-products')
@login_required
def new_products():
    slug = active_slug()
    prompt = ("List 10 trending new construction materials and tools for 2025-2026. "
              "For each: name, use case, approximate cost, and why contractors should care.")
    result = ai_chat([{'role':'user','content':prompt}], slug)
    return render_template('new_products.html', result=result, **ctx())

# ── Locations ──────────────────────────────────────────────────────────────────
@app.route('/locations')
@login_required
def locations():
    return render_template('locations.html', locations=load_locations(active_slug()), **ctx())

@app.route('/location/add', methods=['POST'])
@login_required
def add_location():
    slug = active_slug()
    locs = load_locations(slug)
    locs.append({
        'id': f"LOC-{len(locs)+1:04d}",
        'city':    request.form.get('city'),
        'state':   request.form.get('state'),
        'zipcode': request.form.get('zipcode'),
        'notes':   request.form.get('notes',''),
        'created_at': datetime.datetime.now().isoformat()
    })
    save_locations(locs, slug)
    flash('Location added!','success')
    return redirect(url_for('locations'))

# ── AI Advisor ─────────────────────────────────────────────────────────────────
@app.route('/ai-advisor')
@login_required
def ai_advisor():
    return render_template('ai_advisor.html', **ctx())

@app.route('/api/ask-advisor', methods=['POST'])
@login_required
def ask_advisor():
    slug = active_slug()
    data = request.get_json() or {}
    question = data.get('question','')
    if not question: return jsonify({'error':'No question'}), 400
    bids = load_bids(slug)
    total = sum(float(b.get('total_price',0)) for b in bids)
    system = (f"You are an expert business advisor for contractors. "
              f"This contractor has {len(bids)} bids totaling ${total:.2f}. "
              f"Give specific, actionable advice.")
    response = ai_chat([{'role':'system','content':system},{'role':'user','content':question}], slug)
    return jsonify({'response': response})

# ── AI CEO ─────────────────────────────────────────────────────────────────────
@app.route('/ceo')
@login_required
def ceo_dashboard():
    return render_template('ceo_dashboard.html', **ctx())

@app.route('/api/ceo/analyze', methods=['GET'])
@login_required
def ceo_analyze():
    slug  = active_slug()
    bids  = load_bids(slug)
    prods = load_products(slug)
    total = sum(float(b.get('total_price',0)) for b in bids)
    prompt = (f"Analyze this contracting business: {len(bids)} bids, "
              f"${total:.2f} total bid value, {len(prods)} tracked products. "
              f"Give 3 specific recommendations to win more bids and increase revenue.")
    return jsonify({'analysis': ai_chat([{'role':'user','content':prompt}], slug),
                    'stats': {'bids':len(bids),'total_value':total}})

# ── Settings ───────────────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    slug     = active_slug()
    is_admin = session.get('role') == 'admin'
    if request.method == 'POST':
        key   = request.form.get('openrouter_key','').strip()
        model = request.form.get('openrouter_model','').strip()
        if slug and not is_admin:
            cfg = load_client_config(slug)
            if key:   cfg['openrouter_key']   = key
            if model: cfg['openrouter_model'] = model
            save_client_config(slug, cfg)
        else:
            if key:   set_config('openrouter_key', key)
            if model: set_config('openrouter_model', model)
        flash('Settings saved!','success')
        return redirect(url_for('settings'))
    return render_template('settings.html',
        key_set=bool(get_ai_key(slug)),
        current_key=get_ai_key(slug) or '',
        current_model=get_ai_model(slug), **ctx())

@app.route('/change-password', methods=['GET','POST'])
@login_required
def change_password():
    if request.method == 'POST':
        slug  = active_slug()
        email = session['username']
        old   = request.form.get('old_password','')
        new   = request.form.get('new_password','')
        # Check in tenant users
        if slug:
            users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
            users = load_json(users_path, {})
            if email in users and users[email]['password'] == hash_pw(old):
                users[email]['password'] = hash_pw(new)
                save_json(users_path, users)
                flash('Password changed!','success')
                return redirect(url_for('dashboard'))
        flash('Incorrect password.','error')
    return render_template('change_password.html', **ctx())

# ── Pricing / About (public) ───────────────────────────────────────────────────
@app.route('/pricing')
def pricing_page():
    return render_template('pricing.html', **ctx())

@app.route('/about')
def about():
    return render_template('about.html', **ctx()) if os.path.exists(
        os.path.join(app.template_folder,'about.html')) else redirect(url_for('index'))

# ── Overseer (super admin) ─────────────────────────────────────────────────────
@app.route('/overseer')
@admin_required
def overseer():
    stores = list_client_stores()
    return render_template('overseer.html',
        stores=stores, leads=load_leads(),
        active_count=sum(1 for s in stores if s.get('status')=='active'),
        **ctx())

@app.route('/overseer/client/create', methods=['POST'])
@admin_required
def overseer_create_client():
    store_name = request.form.get('store_name','').strip()
    email      = request.form.get('contact_email','').strip()
    temp_pw    = request.form.get('temp_password','').strip()
    specialty  = request.form.get('specialty','general')
    if not store_name or not email or not temp_pw:
        flash('All fields required.','error')
        return redirect(url_for('overseer'))
    slug = slugify(store_name); base = slug; counter = 1
    while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
        slug = f'{base}-{counter}'; counter += 1
    now = datetime.datetime.now().isoformat()
    save_client_config(slug, {'store_name':store_name,'slug':slug,'contact_email':email,
        'specialty':specialty,'plan':'starter','status':'active','created_at':now})
    save_json(os.path.join(CUSTOMERS_DIR, slug, 'users.json'),
              {email: {'password':hash_pw(temp_pw),'role':'client','store_slug':slug,'created_at':now}})
    flash(f'Client "{store_name}" created! Login: {email} / {temp_pw}','success')
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/impersonate', methods=['POST'])
@admin_required
def overseer_impersonate(slug):
    cfg = load_client_config(slug)
    if not cfg: flash('Store not found.','error'); return redirect(url_for('overseer'))
    session['impersonating_slug'] = slug
    flash(f'Managing {cfg["store_name"]}.','success')
    return redirect(url_for('dashboard'))

@app.route('/overseer/exit')
def overseer_exit():
    session.pop('impersonating_slug', None)
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/suspend', methods=['POST'])
@admin_required
def overseer_suspend(slug):
    cfg = load_client_config(slug)
    if cfg:
        cfg['status'] = 'suspended' if cfg.get('status')=='active' else 'active'
        save_client_config(slug, cfg)
        flash(f'Store {cfg["status"]}.','success')
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/delete', methods=['POST'])
@admin_required
def overseer_delete(slug):
    import shutil
    d = os.path.join(CUSTOMERS_DIR, slug)
    if os.path.exists(d): shutil.rmtree(d)
    flash('Store deleted.','success')
    return redirect(url_for('overseer'))


# ============================================================

# ============================================================
# STRUCTURED LOGGING + METRICS
# ============================================================
import logging as _log, time as _t

import bcrypt as _bcrypt_lib

def _sha256_hash(pw):
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()

def _is_sha256_hash(h):
    return isinstance(h, str) and len(h) == 64 and all(c in '0123456789abcdef' for c in h.lower())

def _bcrypt_hash(pw):
    return _bcrypt_lib.hashpw(pw.encode('utf-8'), _bcrypt_lib.gensalt()).decode('utf-8')

def _bcrypt_verify(pw, stored):
    if _is_sha256_hash(stored):
        return _sha256_hash(pw) == stored, True  # valid, needs_upgrade
    try:
        return _bcrypt_lib.checkpw(pw.encode('utf-8'), stored.encode('utf-8')), False
    except Exception:
        return False, False


_log_handler = _log.StreamHandler()
_log_handler.setFormatter(_log.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_log_handler)
app.logger.setLevel(_log.INFO)

def _ensure_metrics():
    try:
        db = get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL, value REAL DEFAULT 1,
            tenant_slug TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        db.commit()
    except Exception:
        pass

def track(metric, value=1, slug=None):
    try:
        _ensure_metrics()
        get_db().execute(
            "INSERT INTO metrics (metric,value,tenant_slug) VALUES (?,?,?)",
            (metric, value, slug))
        get_db().commit()
    except Exception:
        pass

@app.before_request
def _start_timer():
    from flask import g
    g._start = _t.time()


@app.after_request
def _add_security_headers(response):
    """Security headers on every response."""
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if 'Content-Security-Policy' not in response.headers:
        response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:;"
    return response

@app.after_request
def _log_req(response):
    from flask import g
    if not request.path.startswith('/static'):
        ms = (_t.time() - getattr(g, '_start', _t.time())) * 1000
        if ms > 800:
            app.logger.warning(f"SLOW {request.method} {request.path} {response.status_code} {ms:.0f}ms")
    return response



# ============================================================
# SEO — Sitemap + Robots.txt
# ============================================================
@app.route('/sitemap.xml')
def sitemap():
    """Auto-generated XML sitemap for SEO."""
    host = request.host_url.rstrip('/')
    urls = [
        {'loc': f"{host}/",          'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f"{host}/login",     'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{host}/signup",    'priority': '0.9', 'changefreq': 'monthly'},
        {'loc': f"{host}/pricing",   'priority': '0.8', 'changefreq': 'monthly'},
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"  <url>")
        xml.append(f"    <loc>{u['loc']}</loc>")
        xml.append(f"    <changefreq>{u['changefreq']}</changefreq>")
        xml.append(f"    <priority>{u['priority']}</priority>")
        xml.append(f"  </url>")
    xml.append('</urlset>')
    return '\n'.join(xml), 200, {'Content-Type': 'application/xml'}

@app.route('/robots.txt')
def robots():
    """robots.txt for search engine crawling guidance."""
    host = request.host_url.rstrip('/')
    content = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /overseer
Disallow: /api/
Sitemap: {host}/sitemap.xml
"""
    return content, 200, {'Content-Type': 'text/plain'}


# GLOBAL ERROR HANDLERS
# ============================================================
@app.errorhandler(404)
def not_found_error(e):
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Not found'}), 404
    return render_template('404.html') if os.path.exists(
        os.path.join(app.template_folder or 'templates', '404.html')
    ) else ('<h1>404 - Page Not Found</h1>', 404)

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"UNHANDLED_500: {str(e)}", exc_info=True)
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Internal server error'}), 500
    return '<h1>500 - Something went wrong. We are looking into it.</h1>', 500

@app.errorhandler(429)
def rate_limit_error(e):
    return __import__('flask').jsonify({'error': 'Too many requests. Please slow down.'}), 429


# ── Admin-only API token UI routes ───────────────────────────────────────────
@app.route('/api/token/ui', methods=['POST'])
def api_token_ui_generate():
    if not session.get('role') == 'admin':
        return jsonify({'error': 'Admin only'}), 403
    import secrets as _s, hashlib as _h, datetime as _dt
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    label = 'ui-generated'
    raw_token = _s.token_urlsafe(48)
    token_hash = _h.sha256(raw_token.encode()).hexdigest()
    expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=365)).isoformat()
    conn = get_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS api_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, token_hash TEXT UNIQUE, label TEXT, expires_at TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, label))
        conn.execute('INSERT INTO api_tokens (user_id,token_hash,label,expires_at) VALUES (?,?,?,?)', (user_id, token_hash, label, expires_at))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True,'api_token':raw_token,'expires_at':expires_at})

@app.route('/api/token/ui', methods=['DELETE'])
def api_token_ui_revoke():
    if not session.get('role') == 'admin':
        return jsonify({'error': 'Admin only'}), 403
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    conn = get_db()
    try:
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, 'ui-generated'))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True})


# ── API Key Infrastructure ────────────────────────────────────────────────────
import secrets as _api_secrets, hashlib as _api_hash, functools as _api_functools

_API_KEYS_FILE = os.path.join(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data'), 'api_keys.json')

def _load_api_keys():
    if os.path.exists(_API_KEYS_FILE):
        with open(_API_KEYS_FILE) as f:
            import json as _j; return _j.load(f)
    return {}

def _save_api_keys(keys):
    with open(_API_KEYS_FILE, 'w') as f:
        import json as _j; _j.dump(keys, f, indent=2)

def _require_api_key(f):
    """Decorator: require valid API key via X-API-Key header, Authorization: Bearer, or ?api_key= param."""
    @_api_functools.wraps(f)
    def decorated(*args, **kwargs):
        key = (request.headers.get('X-API-Key') or
               request.args.get('api_key') or
               (request.headers.get('Authorization','')[7:].strip() if request.headers.get('Authorization','').startswith('Bearer ') else None))
        if not key:
            return jsonify({'error': 'API key required. Pass as X-API-Key header or Authorization: Bearer <key>'}), 401
        keys = _load_api_keys()
        if key not in keys:
            return jsonify({'error': 'Invalid API key'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/api-generator')
@login_required
def _admin_api_generator_page():
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    keys = _load_api_keys()
    new_key = request.args.get('new_key', '')
    base_url = request.host_url.rstrip('/')
    return render_template('admin_api_generator.html',
        api_keys=keys, new_key=new_key, base_url=base_url,
        endpoints=[('GET', '/api/bids', 'Get all bids'), ('GET', '/api/bids/<bid_id>', 'Get a single bid'), ('GET', '/api/stats', 'Get dashboard stats'), ('GET', '/api/locations', 'Get all locations'), ('GET', '/health', 'Health check (no auth)')])

@app.route('/admin/api-generator/generate', methods=['POST'])
@login_required
def _admin_api_generate():
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    from datetime import datetime as _dt
    label = request.form.get('label','Testing Key').strip() or 'Testing Key'
    raw_key = 'cpa_' + _api_secrets.token_urlsafe(32)
    keys = _load_api_keys()
    keys[raw_key] = {'name': label, 'created_by': 'admin', 'created_at': _dt.utcnow().isoformat(), 'active': True}
    _save_api_keys(keys)
    flash(f'API key generated!', 'success')
    return redirect('/admin/api-generator?new_key=' + raw_key)

@app.route('/admin/api-generator/revoke/<path:key>', methods=['POST'])
@login_required
def _admin_api_revoke(key):
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    keys = _load_api_keys()
    if key in keys:
        del keys[key]
        _save_api_keys(keys)
        flash('Key revoked.', 'success')
    return redirect('/admin/api-generator')

# ── Public API ───────────────────────────────────────────────────────────────
@app.route('/api/bids', methods=['GET'])
@_require_api_key
def _api_get_bids():
    """Return all bids for the store."""
    try:
        bids = load_bids() if callable(globals().get('load_bids')) else []
        return jsonify({'count': len(bids), 'bids': bids})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bids/<bid_id>', methods=['GET'])
@_require_api_key
def _api_get_bid(bid_id):
    try:
        bids = load_bids() if callable(globals().get('load_bids')) else []
        bid = next((b for b in bids if str(b.get('id','')) == str(bid_id)), None)
        if not bid: return jsonify({'error': 'Bid not found'}), 404
        return jsonify(bid)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
@_require_api_key
def _api_cpa_stats():
    try:
        bids = load_bids() if callable(globals().get('load_bids')) else []
        return jsonify({'total_bids': len(bids), 'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/locations', methods=['GET'])
@_require_api_key
def _api_cpa_locations():
    try:
        locs = load_locations() if callable(globals().get('load_locations')) else []
        return jsonify({'count': len(locs), 'locations': locs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)


# ── Email / SMTP ───────────────────────────────────────────────────────────────

def get_smtp_config():
    """Load SMTP settings from env vars.
    Set these in Railway: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    return {
        'host':     os.environ.get('SMTP_HOST', ''),
        'port':     int(os.environ.get('SMTP_PORT', 587)),
        'user':     os.environ.get('SMTP_USER', ''),
        'password': os.environ.get('SMTP_PASSWORD', ''),
        'from':     os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', 'noreply@example.com')),
    }

def send_email(to, subject, body):
    """Send plain-text email via SMTP. Returns (True, '') or (False, error_msg).
    If SMTP is not configured, logs to console instead (safe — never leaks to browser).
    """
    cfg = get_smtp_config()
    if not cfg['host'] or not cfg['user'] or not cfg['password']:
        # SMTP not configured — log to console only, never show token on screen
        print(f'[EMAIL-NOOP] To: {to} | Subject: {subject}', flush=True)
        print(f'[EMAIL-NOOP] Body: {body}', flush=True)
        return False, 'SMTP not configured'
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From']    = cfg['from']
        msg['To']      = to
        if cfg['port'] == 465:
            with smtplib.SMTP_SSL(cfg['host'], 465, timeout=15) as s:
                s.login(cfg['user'], cfg['password'])
                s.sendmail(cfg['from'], [to], msg.as_string())
        else:
            with smtplib.SMTP(cfg['host'], cfg['port'], timeout=15) as s:
                s.ehlo(); s.starttls()
                s.login(cfg['user'], cfg['password'])
                s.sendmail(cfg['from'], [to], msg.as_string())
        return True, ''
    except Exception as e:
        return False, str(e)


# ── Forgot / Reset Password ────────────────────────────────────────────────────

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    import hashlib as _hl, secrets as _sec, datetime as _dt
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        # Check if email exists across all tenants
        found = False
        token = _sec.token_urlsafe(24)
        # Save reset token
        import os as _os
        resets_path = _os.path.join(DATA_DIR, 'password_resets.json')
        resets = []
        try:
            if _os.path.exists(resets_path):
                with open(resets_path) as f: resets = json.load(f)
        except: pass
        # Check tenant users
        for store in list_client_stores():
            upath = _os.path.join(CUSTOMERS_DIR, store['slug'], 'users.json')
            if not _os.path.exists(upath): continue
            with open(upath) as f:
                users = json.load(f)
            if email in users:
                found = True
                resets = [r for r in resets if r.get('email') != email]
                resets.append({
                    'email': email, 'token': token, 'slug': store['slug'],
                    'expires': (_dt.datetime.now() + _dt.timedelta(hours=2)).isoformat(),
                    'created': _dt.datetime.now().isoformat()
                })
                break
        if found:
            with open(resets_path, 'w') as f: json.dump(resets, f, indent=2)
            reset_url = request.host_url.rstrip('/') + f'/reset-password/{token}'
            send_email(
                to=email,
                subject='Reset Your Password',
                body=(
                    "Hi,\n\n"
                    "A password reset was requested for your account.\n\n"
                    "Click this link to set a new password (valid for 2 hours):\n"
                    + reset_url +
                    "\n\nIf you didn't request this, you can safely ignore this email.\n\n"
                    "-- Support"
                )
            )
            flash('If that email is registered, a reset link has been sent.', 'info')
        else:
            # Don't reveal if email exists
            flash('If that email is registered, a reset link has been generated.', 'info')
        return redirect(url_for('forgot_password'))
    return render_template('forgot_password.html', **ctx())

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    import os as _os, datetime as _dt
    resets_path = _os.path.join(DATA_DIR, 'password_resets.json')
    resets = []
    try:
        if _os.path.exists(resets_path):
            with open(resets_path) as f: resets = json.load(f)
    except: pass
    reset = next((r for r in resets if r.get('token') == token), None)
    if not reset:
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('login'))
    if _dt.datetime.fromisoformat(reset['expires']) < _dt.datetime.now():
        flash('Reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        new_pw = request.form.get('password', '').strip()
        if len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token, **ctx())
        # Update password
        slug = reset['slug']
        email = reset['email']
        upath = _os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        with open(upath) as f: users = json.load(f)
        if email in users:
            users[email]['password'] = hash_pw(new_pw)
            with open(upath, 'w') as f: json.dump(users, f, indent=2)
        # Remove used token
        resets = [r for r in resets if r.get('token') != token]
        with open(resets_path, 'w') as f: json.dump(resets, f, indent=2)
        flash('Password updated! You can now sign in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, email=reset.get('email',''), **ctx())
