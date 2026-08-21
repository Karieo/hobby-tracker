"""Warhammer Collection Tracker — Flask server.

Session 1 scope is the spec's build steps 1 and 2: schema, migration runner,
reference-data seed, and the BSData/Munitorum importer. So this is deliberately
a skeleton — auth, the health endpoint, and a status page that shows what the
importer put in the database. The collection routes arrive in step 3.

Auth posture matches Remndrs, because the Cloudflare Tunnel makes this publicly
reachable and obscurity is not a plan: bcrypt password, session cookie, a
before_request allowlist, per-IP failed-login throttling, and ProxyFix so the
tunnel's forwarded headers are trusted.
"""

import logging
import os
import secrets
import time
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def apply_timezone():
    """Pin the process timezone from TIMEZONE.

    Every timestamp here is naive local wall-clock — stage_changed_at drives
    "what have I finished lately", and on a container defaulting to UTC that
    reads hours off. Unset keeps the host zone.
    """
    tz = os.getenv('TIMEZONE', '').strip()
    if not tz:
        return
    os.environ['TZ'] = tz
    try:
        time.tzset()
    except AttributeError:
        logging.getLogger('tracker').warning(
            'TIMEZONE set but time.tzset() is unavailable on this OS')


apply_timezone()

import bcrypt  # noqa: E402
from flask import (Flask, jsonify, redirect, render_template,  # noqa: E402
                   request, session)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import database as db  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('tracker')

app = Flask(__name__)

# Behind the Cloudflare tunnel the request arrives over plain http on localhost,
# so Flask would otherwise rebuild request.url with the wrong scheme and host,
# and every remote_addr would read as 127.0.0.1 — which would make the login
# throttle throttle everyone at once.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024   # box photos, later

_secret = os.getenv('SESSION_SECRET')
if not _secret:
    _secret = secrets.token_hex(32)
    log.warning('SESSION_SECRET not set — generated a random one '
                '(sessions will not survive a restart)')
app.secret_key = _secret
app.permanent_session_lifetime = timedelta(days=30)

VERSION = '0.1.0'


# ── Bootstrap ────────────────────────────────────────────

def seed_owner():
    if db.count_users() > 0:
        return
    name = os.getenv('OWNER_NAME', 'Clay')
    password = os.getenv('OWNER_PASSWORD', 'changeme')
    db.create_user(name, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
    log.info('Seeded owner account "%s"', name)


db.init_db()
seed_owner()


# ── Auth ─────────────────────────────────────────────────

PUBLIC_PATHS = ('/login', '/api/auth/login', '/api/version', '/healthz', '/static/')

# Per-IP failed-login throttle. In memory on purpose: a restart clearing it is
# fine for a single-user app on Clay's own hardware.
_AUTH_FAILURES = {}
_THROTTLE_WINDOW = 15 * 60
_THROTTLE_MAX = 8


def _auth_throttled(ip):
    now = time.time()
    fresh = [t for t in _AUTH_FAILURES.get(ip, []) if now - t < _THROTTLE_WINDOW]
    if fresh:
        _AUTH_FAILURES[ip] = fresh
    else:
        _AUTH_FAILURES.pop(ip, None)
    return len(fresh) >= _THROTTLE_MAX


def _record_auth_failure(ip):
    _AUTH_FAILURES.setdefault(ip, []).append(time.time())
    log.warning('Failed login attempt from %s', ip)


@app.before_request
def require_login():
    path = request.path
    if any(path == p or path.startswith(p) for p in PUBLIC_PATHS):
        return None
    if session.get('user_id'):
        return None
    if path.startswith('/api/'):
        return jsonify({'error': 'Unauthorized'}), 401
    return redirect('/login')


@app.route('/login')
def login_page():
    return render_template('login.html', owner=os.getenv('OWNER_NAME', 'Clay'))


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    if _auth_throttled(request.remote_addr):
        return jsonify({'error': 'Too many attempts — try again later'}), 429
    data = request.get_json(silent=True) or request.form
    user = db.get_user_by_login(data.get('login') or '')
    password = data.get('password') or ''
    if not user or not bcrypt.checkpw(password.encode(),
                                      user['password_hash'].encode()):
        _record_auth_failure(request.remote_addr)
        return jsonify({'error': 'Invalid credentials'}), 401
    _AUTH_FAILURES.pop(request.remote_addr, None)
    session.permanent = True
    session['user_id'] = user['id']
    return jsonify({'success': True, 'user': {'name': user['name']}})


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})


# ── Status ───────────────────────────────────────────────

@app.route('/healthz')
def healthz():
    return jsonify({'ok': True, 'version': VERSION})


@app.route('/api/version')
def api_version():
    return jsonify({'version': VERSION})


@app.route('/')
def index():
    """What the importer has loaded. The collection views land in step 3."""
    with db.connect() as conn:
        unresolved = [dict(r) for r in db.open_unresolved(conn)]
        stages = [dict(r) for r in db.get_stages(conn)]
    return render_template('index.html', summary=db.import_summary(),
                           stages=stages, unresolved=unresolved,
                           owner=os.getenv('OWNER_NAME', 'Clay'),
                           version=VERSION)


if __name__ == '__main__':
    port = int(os.getenv('PORT', '3100'))
    log.info('Warhammer Collection Tracker v%s on :%s', VERSION, port)
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG') == '1')
