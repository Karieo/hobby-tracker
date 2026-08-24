"""Warhammer Collection Tracker — Flask server.

Reference data, the collection and its stage pipeline, lists and the gap
report. Routes live here and delegate to the flat modules beside it —
``collection.py``, ``lists.py``, ``kit_templates.py``, ``photos.py``.

The barcode scanner used to live here too and is gone: Clay measured it slower
than typing what is in a box he is holding.

Auth posture matches Remndrs, because the Cloudflare Tunnel makes this publicly
reachable and obscurity is not a plan: bcrypt password, session cookie, a
before_request allowlist, per-IP failed-login throttling, and ProxyFix so the
tunnel's forwarded headers are trusted.
"""

import csv
import hashlib
import io
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from datetime import date, timedelta
from urllib.parse import urlencode

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
from flask import (Flask, Response, abort, jsonify, redirect,  # noqa: E402
                   render_template, request, send_file, session)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import bulk_add  # noqa: E402
import collection as col
import list_allocate
import list_parse
import list_resolve
import journey
import lists as army_lists
import photos
import rules_data  # noqa: E402
import database as db  # noqa: E402
import kit_templates as templates  # noqa: E402

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

VERSION = '0.3.0'
_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _asset_version():
    """Cache-buster for app.css/app.js, from their mtimes.

    Without it a deploy's fix hides behind a browser's cached copy — which is
    exactly what happened while building this, and would be far more confusing
    on a phone that has had the page open for a week.
    """
    stamps = []
    for rel in ('static/css/app.css', 'static/js/app.js'):
        try:
            stamps.append(int(os.path.getmtime(os.path.join(_APP_DIR, rel))))
        except OSError:
            pass
    return str(max(stamps)) if stamps else VERSION


_ASSET_VERSION = _asset_version()


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


# Paths a bearer token may reach. Deliberately not "every /api/ route": this
# is the first consumer `api_tokens` has ever had, and a token that can read
# the inventory is a very different thing to leave in a script's config file
# than one that can delete a kit. Widening it is one entry in this tuple, and
# should be a decision rather than a side effect.
TOKEN_PATHS = ('/api/export/',)


def _bearer_token():
    header = request.headers.get('Authorization', '')
    scheme, _, value = header.partition(' ')
    return value.strip() if scheme.lower() == 'bearer' and value.strip() else None


def _user_for_token(token):
    """SHA-256, not bcrypt.

    Passwords get bcrypt because they are low-entropy and a fast hash makes
    them guessable offline. An API token is 256 bits of `secrets` output, so
    there is nothing to guess — and a salted hash could not be looked up by
    index at all, forcing a scan of every token on every request.

    `database.get_user_by_token_hash` stamps `last_used_at` on the way through,
    which is the only way to tell a live token from a forgotten one later.
    """
    digest = hashlib.sha256(token.encode()).hexdigest()
    return db.get_user_by_token_hash(digest)


@app.before_request
def require_login():
    path = request.path
    if any(path == p or path.startswith(p) for p in PUBLIC_PATHS):
        return None
    if session.get('user_id'):
        return None
    if any(path.startswith(p) for p in TOKEN_PATHS):
        token = _bearer_token()
        if token and _user_for_token(token):
            return None
        # Not throttled: a 256-bit random token is not brute-forceable, and
        # throttling by IP would lock out the one script that is meant to be
        # calling this. Logged, because a stream of these means a stale token
        # in something Clay forgot he set up.
        if token:
            log.warning('Rejected API token from %s', request.remote_addr)
        return jsonify({'error': 'Unauthorized'}), 401
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


@app.route('/reference')
def reference():
    """What the importer loaded, and what it could not resolve."""
    with _read() as conn:
        unresolved = [dict(r) for r in db.open_unresolved(conn)]
        stages = col.stage_ladder(conn)
        # Local only. "Has upstream moved past the pin?" needs the network and
        # belongs to the weekly sweep; a page that could not render because
        # GitHub was down would be a worse page.
        provenance = rules_data.provenance(conn)
    return render_template('reference.html', summary=db.import_summary(),
                           stages=stages, unresolved=unresolved,
                           rules=provenance)


# ── Connections ──────────────────────────────────────────
#
# Two helpers rather than one, so a handler declares its intent: reads never
# hold a write transaction open, and writes commit exactly once at the end.

@contextmanager
def _read():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write():
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _payload():
    return request.get_json(silent=True) or request.form or {}


def _int(value, default=None):
    """Form values arrive as strings and empty means "not set", not zero."""
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Money is stored in minor units (cents) everywhere and only ever becomes a
# symbol at the edge. CURRENCY names which symbol; USD because that is where
# Clay buys. A setting rather than a constant because the symbol was hardcoded
# in three templates with two different format strings, which is how a fourth
# one ends up in a third format — and because if this is ever shared, someone
# in a different country should not have to edit templates.
CURRENCY = (os.getenv('CURRENCY') or 'USD').strip().upper()
CURRENCY_SYMBOLS = {'USD': '$', 'GBP': '£', 'EUR': '€', 'CAD': 'CA$',
                    'AUD': 'A$', 'NZD': 'NZ$'}
CURRENCY_SYMBOL = CURRENCY_SYMBOLS.get(CURRENCY, CURRENCY + ' ')


def _money(value):
    """A typed amount to minor units. None for blank, None for nonsense.

    The way in, where `money()` is the way out. It used to be
    `round(float(price) * 100) if price else None`, written inline in four
    places — which meant four chances to drift and, more to the point, an
    uncaught ValueError and a 500 the first time something non-numeric
    arrived. A price nobody can parse is a price nobody typed.

    Strips the currency symbol and thousands separators, because a value
    pasted back out of the app arrives with them.
    """
    if value is None:
        return None
    text = str(value).strip().lstrip(CURRENCY_SYMBOL).replace(',', '').strip()
    if not text:
        return None
    try:
        return round(float(text) * 100)
    except ValueError:
        return None


@app.template_filter('money')
def money(cents):
    """Minor units to a readable amount, or an em dash for nothing.

    None and 0 both render as "—" on purpose: a kit with no recorded price and
    a kit that cost nothing are the same fact on screen — nobody typed a price
    — and "$0.00" reads like a claim that it was free.
    """
    if not cents:
        return '—'
    return f'{CURRENCY_SYMBOL}{cents / 100:,.2f}'


def filter_url(_path=None, **overrides):
    """This page's URL with some query args changed and the rest kept.

    The chip rail used to hand-build `?system=wh40k&q=...`, which meant every
    chip silently dropped whatever else was set — tap "40k" while filtered to
    Orks and the faction went away. With seven filters that stops being a wart
    and becomes the reason nobody uses more than one.

    A value of None or '' removes the key rather than sending it empty.
    """
    # Empty values are dropped on the way in as well as on the way out. A GET
    # form submits every field it has, so "faction: Orks" arrives as
    # ?q=&system=&faction_id=1&stage_id=&points_min=&points_max= — and without
    # this every chip would then carry that litter forward for the rest of the
    # session.
    args = {k: v for k, v in request.args.to_dict().items() if v != ''}
    for key, value in overrides.items():
        if value in (None, ''):
            args.pop(key, None)
        else:
            args[key] = str(value)
    # `_path` sends the same filters somewhere else — the CSV download is this
    # page's URL with a different extension, and building it by hand in the
    # template is how a link starts dropping filters the page still shows.
    path = _path or request.path
    query = urlencode(sorted(args.items()))
    return f'{path}?{query}' if query else path


@app.context_processor
def inject_globals():
    return {'owner': os.getenv('OWNER_NAME', 'Clay'), 'version': VERSION,
            'filter_url': filter_url,
            'asset_version': _ASSET_VERSION, 'currency': CURRENCY,
            'currency_symbol': CURRENCY_SYMBOL}


# ── Armies ───────────────────────────────────────────────

@app.route('/')
def index():
    """Home, from Tracker Wireframes §3a. One number, then the mass, then which
    army is dragging — and then one named unit to pick back up.

    That last part is why this replaced the armies index here. An index answers
    "what do I have", which is a question Clay can already answer; it does not
    answer "what do I do now", and a tracker that only keeps score is one he
    stops opening. The armies list keeps its own screen at /armies.
    """
    with _read() as conn:
        return render_template(
            'home.html',
            summary=col.home_summary(conn),
            armies=[a for a in col.list_armies(conn) if a['model_count']],
            stalled=col.stalled_unit(conn))


@app.route('/armies')
def armies_page():
    with _read() as conn:
        armies = col.list_armies(conn)
        factions = col.list_factions(conn)
        summary = db.import_summary()
    return render_template('armies.html', armies=armies, factions=factions,
                           summary=summary)


#: The chip filters, as predicates over rows already loaded. They are
#: questions about what is on screen rather than about the database, so they
#: run here and the counts underneath them cannot disagree.
_COLLECTION_CHIPS = {
    'unpainted': lambda r: r['done_count'] < r['owned_count'],
    'sealed': lambda r: r['sealed_boxes'],
}


def _collection_filters():
    """The collection screen's filters, read off the query string.

    One function because two routes render the same rows — the page and its
    CSV — and a download that quietly ignored a filter the screen was showing
    would be the worst kind of wrong: a spreadsheet that looks right.
    """
    query = (request.args.get('q') or '').strip()
    return {
        'query': query,
        # Three states rather than the old "unowned appear only when
        # searching": mine (the inventory), wanted (the shopping list),
        # everything (the catalogue, which is what the own-it check needs).
        # Searching still opens it up by default, so the shop question is one
        # box as before — but now it can be said rather than inferred.
        'own': request.args.get('own') or ('all' if query else 'mine'),
        'sort': request.args.get('sort') or 'name',
        'faction_id': _int(request.args.get('faction_id')),
        'system': request.args.get('system') or None,
        'stage_id': _int(request.args.get('stage_id')),
        'points_min': _int(request.args.get('points_min')),
        'points_max': _int(request.args.get('points_max')),
        'chip': request.args.get('filter') or '',
    }


def _collection_rows(conn, f):
    """The rows those filters select, chips included."""
    rows = col.inventory(
        conn, query=f['query'] or None, faction_id=f['faction_id'],
        game_system=f['system'], stage_id=f['stage_id'],
        points_min=f['points_min'], points_max=f['points_max'],
        only_wanted=(f['own'] == 'wanted'), sort=f['sort'],
        include_unowned=(f['own'] == 'all'))
    keep = _COLLECTION_CHIPS.get(f['chip'])
    return [r for r in rows if keep(r)] if keep else rows


def _collection_totals(rows):
    return {
        'datasheets': len(rows),
        'owned': sum(r['owned_count'] for r in rows),
        'built': sum(r['built_count'] for r in rows),
        'done': sum(r['done_count'] for r in rows),
        'wanted': sum(r['wanted_count'] for r in rows),
        'sealed': sum(r['sealed_boxes'] for r in rows),
    }


@app.route('/collection')
def collection_page():
    """What I own, how many, and what state — and, with a query, the own-it
    check: the same screen answers "you own none of these" from a shop."""
    # The filter form is a GET form, so it submits its empty fields too. One
    # hop to the tidy URL and everything after — a bookmark, a chip, the link
    # Clay sends himself — is the short one. Nothing to loop on: the cleaned
    # URL has no empty values left to strip.
    if any(v == '' for v in request.args.values()):
        return redirect(filter_url())

    f = _collection_filters()
    with _read() as conn:
        rows = _collection_rows(conn, f)
        return render_template(
            'collection.html', rows=rows, query=f['query'], filter=f['chip'],
            system=(request.args.get('system') or ''),
            faction_id=f['faction_id'], stage_id=f['stage_id'],
            points_min=request.args.get('points_min') or '',
            points_max=request.args.get('points_max') or '',
            own=f['own'], sort=f['sort'], sorts=col.INVENTORY_SORT_LABELS,
            factions=col.list_factions(conn),
            stages=col.stage_ladder(conn),
            totals=_collection_totals(rows))


#: The download's columns. Everything a row can answer without nesting, in the
#: order the screen reads: what it is, how many, how far along.
COLLECTION_CSV_COLUMNS = (
    ('name', 'name'),
    ('faction', 'faction_name'),
    ('game_system', 'game_system'),
    ('owned', 'owned_count'),
    ('built', 'built_count'),
    ('battle_ready', 'done_count'),
    ('wanted', 'wanted_count'),
    ('sealed_boxes', 'sealed_boxes'),
    ('units', 'unit_count'),
    ('kits', 'kit_count'),
    ('effort_total', 'effort_total'),
    ('effort_done', 'effort_done'),
    ('completion_pct', 'completion'),
    ('points_low', 'points_low'),
    ('points_high', 'points_high'),
    ('last_activity', 'last_activity'),
)


@app.route('/collection.csv')
def collection_csv():
    """The collection screen, downloadable.

    Clay: "Do a csv that I can download from the site."

    Deliberately *not* `/api/export/inventory?format=csv`, which is the other
    CSV in this app and answers a different question. That one is per
    datasheet for a list optimiser and knows only `army_id` — point a button
    on this screen at it and filtering to "Orks, unpainted" would download
    every Ork including the painted ones. A spreadsheet that looks right and
    is not is worse than no button.

    So this renders the rows the screen just computed, through the same
    `_collection_filters` and `_collection_rows` the page uses. The two cannot
    drift, because there is one of each.

    Effort is here alongside the raw counts, never instead: a Knight and a
    Termagant are both "1 model", which is what makes a model-count percentage
    meaningless.
    """
    f = _collection_filters()
    with _read() as conn:
        rows = _collection_rows(conn, f)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for label, _ in COLLECTION_CSV_COLUMNS])
    for row in rows:
        writer.writerow([row[key] for _, key in COLLECTION_CSV_COLUMNS])
    return Response(buffer.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition':
            f'attachment; filename="{_csv_filename(f)}"'})


def _csv_filename(f):
    """Named for what is in it, so a folder of these is still readable.

    A download called `collection.csv` four times over is four files called
    `collection (3).csv`, and no way to tell the Orks from the Knights.
    """
    parts = ['collection']
    if f['faction_id']:
        with _read() as conn:
            found = conn.execute('SELECT slug FROM factions WHERE id = ?',
                                 (f['faction_id'],)).fetchone()
        if found:
            parts.append(found['slug'])
    if f['chip']:
        parts.append(f['chip'])
    if f['own'] != 'mine':
        parts.append(f['own'])
    return '-'.join(parts) + '.csv'


# ── Lists (spec §2.6) ────────────────────────────────────
#
# The only part of the app that pulls. Everything else waits for Clay to feel
# like moving a model; a list names a target and says what stands in the way.

@app.route('/lists')
def lists_page():
    with _read() as conn:
        return render_template('lists.html', lists=army_lists.list_lists(conn),
                               factions=col.list_factions(conn),
                               wants=army_lists.wishlist(conn))


@app.route('/lists/<int:list_id>')
def list_page(list_id):
    """The gap report, re-run against the collection on every load.

    Deliberately not stored. "Paint three Meganobz, reload the list, the
    numbers move. That feedback loop is the feature" — and a cached report
    would quietly stop being true the moment Clay picked up a brush.
    """
    include_unassigned = _flag(request.args.get('include_unassigned'), True)
    with _read() as conn:
        army_list = army_lists.get_list(conn, list_id)
        if not army_list:
            abort(404)
        gap = list_allocate.allocate(conn, list_id,
                                     include_unassigned=include_unassigned)
        return render_template('list.html', list=army_list, gap=gap,
                               include_unassigned=include_unassigned,
                               assigned=_assigned_models(conn, gap))


@app.route('/api/lists', methods=['POST'])
def api_create_list():
    data = _payload()
    try:
        with _write() as conn:
            list_id = army_lists.create_list(
                conn, data.get('name') or '',
                faction_id=_int(data.get('faction_id')),
                detachment=(data.get('detachment') or '').strip() or None,
                points_limit=_int(data.get('points_limit')))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'id': list_id}), 201


@app.route('/api/lists/<int:list_id>', methods=['DELETE'])
def api_delete_list(list_id):
    with _write() as conn:
        if not army_lists.get_list(conn, list_id):
            abort(404)
        army_lists.delete_list(conn, list_id)
    return jsonify({'success': True})


@app.route('/api/lists/<int:list_id>/entries', methods=['POST'])
def api_add_entry(list_id):
    data = _payload()
    try:
        with _write() as conn:
            entry_id = army_lists.add_entry(
                conn, list_id, _int(data.get('datasheet_id')),
                _int(data.get('model_count'), 1))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'id': entry_id}), 201


@app.route('/api/lists/entries/<int:entry_id>', methods=['DELETE'])
def api_remove_entry(entry_id):
    with _write() as conn:
        army_lists.remove_entry(conn, entry_id)
    return jsonify({'success': True})


@app.route('/api/lists/<int:list_id>/entries/<int:entry_id>', methods=['PATCH'])
def api_resolve_entry(list_id, entry_id):
    """Say which datasheet an unresolved line meant, and never be asked again.

    The alias write-back lives inside `list_resolve.resolve_entry` rather than
    here, so no route can forget it. The re-run report comes back in the same
    response because the numbers move the moment the row resolves — the whole
    list may go from "3 units short" to fieldable on one tap.
    """
    data = _payload()
    datasheet_id = _int(data.get('datasheet_id'))
    if not datasheet_id:
        return jsonify({'error': 'Pick a datasheet'}), 400
    try:
        with _write() as conn:
            if not army_lists.get_list(conn, list_id):
                abort(404)
            list_resolve.resolve_entry(conn, entry_id, datasheet_id)
            gap = list_allocate.allocate(conn, list_id)
        return jsonify({'gap': _summary_only(gap)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/lists/<int:list_id>/reparse', methods=['POST'])
def api_reparse_list(list_id):
    """Read the stored paste again with today's parser.

    Safe to lose the entries: resolving a line by hand wrote an alias, and the
    alias is the first thing resolution consults. The knowledge lives in the
    alias table rather than in the rows.
    """
    try:
        with _write() as conn:
            result = army_lists.reparse(conn, list_id)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/units/<int:unit_id>/built-as', methods=['POST'])
def api_built_as(unit_id):
    """What a multi-option kit actually got built as, and whether it swaps.

    Section 7 asks for this as a prompt when a model advances to assembled.
    Here there is nothing to interrupt for: `add_models` stamps every model
    with its unit's datasheet at creation, so a model is never uncommitted and
    the auto-fill the spec describes has already happened. What is left is the
    case auto-fill cannot answer — a box that builds several things, where the
    unit's datasheet is a default rather than a decision — so it is a control
    on the unit rather than a modal in the way of a tap.
    """
    data = _payload()
    datasheet_id = _int(data.get('datasheet_id'))
    flexible = bool(data.get('is_flexible'))
    with _write() as conn:
        unit = col.get_unit(conn, unit_id)
        if not unit:
            abort(404)
        try:
            col.set_built_as(conn, unit_id, datasheet_id, flexible=flexible)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})


@app.route('/api/lists/<int:list_id>/wishlist', methods=['POST'])
def api_raise_wishlist(list_id):
    """Turn the buy half of the gap into wants. The handoff to the shop."""
    try:
        with _write() as conn:
            if not army_lists.get_list(conn, list_id):
                abort(404)
            added = army_lists.raise_wishlist(conn, list_id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'added': added})


@app.route('/api/datasheets/<int:datasheet_id>/basing', methods=['POST'])
def api_set_basing(datasheet_id):
    """Whether this datasheet's models have a base. Clay's call, never ours —
    the rules data cannot tell them apart. See migration 004."""
    basing = (_payload().get('basing') or '').strip() or None
    try:
        with _write() as conn:
            col.set_basing(conn, datasheet_id, basing)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True, 'basing': basing})


@app.route('/api/collection/<int:datasheet_id>')
def api_owned_summary(datasheet_id):
    """One datasheet's ownership, for a scan that asks before it adds."""
    with _read() as conn:
        summary = col.owned_summary(conn, datasheet_id)
        if not summary:
            abort(404)
        return jsonify(summary)


@app.route('/api/armies', methods=['POST'])
def api_create_army():
    data = _payload()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'An army needs a name'}), 400
    with _write() as conn:
        army_id = col.create_army(conn, name,
                                  primary_faction_id=_int(data.get('primary_faction_id')),
                                  notes=(data.get('notes') or '').strip() or None)
    return jsonify({'id': army_id}), 201


@app.route('/api/armies/<int:army_id>', methods=['PATCH'])
def api_update_army(army_id):
    data = _payload()
    with _write() as conn:
        if not col.update_army(
                conn, army_id, name=(data.get('name') or '').strip() or None,
                primary_faction_id=_int(data.get('primary_faction_id')),
                notes=data.get('notes')):
            abort(404)
    return jsonify({'success': True})


@app.route('/armies/unassigned')
@app.route('/armies/<int:army_id>')
def army_detail(army_id=None):
    """The screen Clay lives in.

    Unassigned shares this template deliberately: units with no army are a real
    bucket that needs the same stats and the same controls, not a special case
    tucked away somewhere quieter.
    """
    with _read() as conn:
        army = col.get_army(conn, army_id) if army_id else None
        if army_id and not army:
            abort(404)
        units = col.list_units(conn, army_id=army_id, unassigned=army_id is None)
        return render_template(
            'army.html', army=army, units=units,
            stats=col.army_stats(conn, army_id),
            stages=col.stage_ladder(conn),
            armies=[a for a in col.list_armies(conn) if a['id']],
            factions=col.list_factions(conn))


# ── Units ────────────────────────────────────────────────

@app.route('/units/<int:unit_id>')
def unit_detail(unit_id):
    with _read() as conn:
        unit = col.get_unit(conn, unit_id)
        if not unit:
            abort(404)
        options = col.buildable_options(conn, unit_id)
        return render_template(
            'unit.html', unit=unit,
            breakdown=col.unit_breakdown(conn, unit_id),
            models=col.unit_models(conn, unit_id),
            stages=col.stage_ladder(conn),
            # Only asked where the box genuinely builds more than one thing.
            # For every other kit the answer is already recorded and there is
            # nothing to prompt for.
            options=options if len(options) > 1 else [],
            built_as=col.unit_built_as(conn, unit_id),
            unit_photos=photos.for_unit(conn, unit_id),
            today=date.today().isoformat(),
            armies=[a for a in col.list_armies(conn) if a['id']])


@app.route('/api/units', methods=['POST'])
def api_create_unit():
    data = _payload()
    datasheet_id = _int(data.get('datasheet_id'))
    model_count = _int(data.get('model_count'), 0)
    if not datasheet_id:
        return jsonify({'error': 'Pick a datasheet'}), 400
    if model_count < 1:
        return jsonify({'error': 'A unit needs at least one model'}), 400
    with _write() as conn:
        # Adding ten more Boyz to the ten already recorded is one squad of
        # twenty, not two rows of ten with nothing to tell them apart.
        added = col.add_or_extend_unit(
            conn, datasheet_id, model_count,
            army_id=_int(data.get('army_id')),
            kit_id=_int(data.get('kit_id')),
            stage_id=_int(data.get('stage_id')),
            nickname=(data.get('nickname') or '').strip() or None)
    return jsonify({'id': added['unit_id'],
                    'extended': added['extended']}), 201


@app.route('/api/units/<int:unit_id>/advance', methods=['POST'])
def api_advance_unit(unit_id):
    """The primary interaction. No body advances the whole unit."""
    data = _payload()
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        moved = col.advance_unit(conn, unit_id, count=_int(data.get('count')),
                                 from_stage_id=_int(data.get('from_stage_id')))
        return jsonify({'moved': moved,
                        'breakdown': col.unit_breakdown(conn, unit_id)})


@app.route('/api/units/<int:unit_id>/retreat', methods=['POST'])
def api_retreat_unit(unit_id):
    """Step models back one stage — the design's −1 control.

    The counterpart to advancing. Every tap in a paint session saves
    immediately with no confirmation, so there has to be a way back that is
    just as cheap, or the app becomes something you are careful with.
    """
    data = _payload()
    with _write() as conn:
        moved = col.retreat_unit(conn, unit_id, count=_int(data.get('count')),
                                 from_stage_id=_int(data.get('from_stage_id')))
        return jsonify({'moved': moved,
                        'breakdown': col.unit_breakdown(conn, unit_id)})


@app.route('/api/units/<int:unit_id>/stage', methods=['POST'])
def api_set_unit_stage(unit_id):
    """Bulk stage set: a hand-picked selection, or "N of them are at X"."""
    data = _payload()
    stage_id = _int(data.get('stage_id'))
    if not stage_id:
        return jsonify({'error': 'Pick a stage'}), 400
    model_ids = data.get('model_ids')
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        if model_ids:
            owned = {m['id'] for m in col.unit_models(conn, unit_id)}
            # Never let one unit's request move another unit's models.
            chosen = [i for i in (_int(x) for x in model_ids) if i in owned]
            moved = col.set_models_stage(conn, chosen, stage_id)
        else:
            moved = col.set_unit_stage_counts(
                conn, unit_id, stage_id, _int(data.get('count'), 0))
        return jsonify({'moved': moved,
                        'breakdown': col.unit_breakdown(conn, unit_id)})


@app.route('/api/units/<int:unit_id>/move', methods=['POST'])
def api_move_unit(unit_id):
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        col.move_unit_to_army(conn, unit_id, _int(_payload().get('army_id')))
    return jsonify({'success': True})


@app.route('/api/units/<int:unit_id>', methods=['PATCH'])
def api_update_unit(unit_id):
    data = _payload()
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        # Only what the form actually sent. A PATCH that names one field must
        # not blank the other — see collection.update_unit.
        col.update_unit(conn, unit_id,
                        **{k: data[k] for k in ('nickname', 'notes')
                           if k in data})
    return jsonify({'success': True})


@app.route('/api/units/<int:unit_id>', methods=['DELETE'])
def api_delete_unit(unit_id):
    """Undo for a mistyped entry — not how you record getting rid of models.

    Models Clay actually owned leave through a kit disposal, which keeps the
    rows and the spend history.
    """
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        col.delete_unit(conn, unit_id)
    return jsonify({'success': True})


@app.route('/api/units/<int:unit_id>/models', methods=['POST'])
def api_add_models(unit_id):
    data = _payload()
    count = _int(data.get('count'), 0)
    if count < 1:
        return jsonify({'error': 'How many?'}), 400
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        stage_id = _int(data.get('stage_id')) or db.first_owned_stage(conn)['id']
        col.add_models(conn, unit_id, count, stage_id)
    return jsonify({'success': True}), 201


@app.route('/api/units/<int:unit_id>/models', methods=['DELETE'])
def api_remove_models(unit_id):
    """Undo for adding too many — not how you record getting rid of models.

    Same distinction the whole-unit delete makes: this deletes rows, so it is
    for plastic that was never there. Models Clay owned and sold leave through
    `POST .../dispose`, which keeps every row and what it went for.
    """
    data = _payload()
    count = _int(data.get('count'), 0)
    if count < 1:
        return jsonify({'error': 'How many?'}), 400
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        result = col.remove_models(conn, unit_id, count)
    return jsonify(result)


@app.route('/api/units/<int:unit_id>/dispose', methods=['POST'])
def api_dispose_models(unit_id):
    """Sold, traded or given away — a number of them, not the whole squad.

    Clay: *"sell, trade/giveaway"*. The mirror of DELETE above and the reason
    both exist: this keeps every row, its stage and what it went for, because
    a disposal is a status change and the spend history is the point. Deleting
    is for plastic that was never there.
    """
    data = _payload()
    count = _int(data.get('count'), 0)
    status = (data.get('status') or 'sold').strip()
    if count < 1:
        return jsonify({'error': 'How many?'}), 400
    if status not in ('sold', 'traded', 'gifted'):
        return jsonify({'error': f'{status!r} is not a disposal'}), 400
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        try:
            result = col.dispose_models(
                conn, unit_id, count, status,
                price_cents=_money(data.get('price')),
                note=(data.get('note') or '').strip() or None)
        except ValueError as err:
            return jsonify({'error': str(err)}), 400
    return jsonify(result)


@app.route('/api/units/<int:unit_id>/wishlist', methods=['POST'])
def api_wishlist_models(unit_id):
    """Want more of these.

    Clay: *"wishlist more"*. No new storage — Wishlist has been position 0 of
    the ladder since the first migration, so this is `add_models` aimed one
    rung below owned. They show at /collection?own=wanted.
    """
    data = _payload()
    count = _int(data.get('count'), 0)
    if count < 1:
        return jsonify({'error': 'How many?'}), 400
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        added = col.wishlist_models(conn, unit_id, count)
    return jsonify({'wishlisted': added})


# ── Photos (spec §2.4) ───────────────────────────────────
#
# A dated log per unit rather than one picture on a column: a squad gets
# photographed on sprue, half-painted and done. The bytes live under
# data/photos/ and backup.sh carries the directory beside the snapshot, so the
# row and its file travel together.

@app.route('/api/units/<int:unit_id>/photos', methods=['POST'])
def api_add_photo(unit_id):
    """Multipart, because this is a phone with a camera roll.

    `taken_on` is what Clay says, not what the clock says: the squad finished
    on Tuesday is photographed then and uploaded on Sunday.
    """
    upload = request.files.get('photo')
    if not upload:
        return jsonify({'error': 'No picture came through'}), 400
    with _write() as conn:
        if not col.get_unit(conn, unit_id):
            abort(404)
        try:
            saved = photos.add(
                conn, unit_id, upload.read(),
                taken_on=(request.form.get('taken_on') or '').strip() or None,
                caption=request.form.get('caption'))
        except photos.PhotoError as err:
            return jsonify({'error': str(err)}), 400
    return jsonify(saved), 201


@app.route('/api/photos/<int:photo_id>', methods=['PATCH'])
def api_update_photo(photo_id):
    """A note after the fact.

    The picture gets taken and uploaded in one motion; what it was worth saying
    about it turns up later. As a field on the upload form and nowhere else, a
    caption had exactly one moment to exist in.
    """
    data = _payload()
    fields = {k: data[k] for k in ('taken_on', 'caption') if k in data}
    if not fields:
        return jsonify({'error': 'Nothing to change'}), 400
    with _write() as conn:
        if not photos.get(conn, photo_id):
            abort(404)
        photos.update(conn, photo_id, **fields)
    return jsonify({'success': True})


@app.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def api_delete_photo(photo_id):
    with _write() as conn:
        unit_id = photos.delete(conn, photo_id)
    if unit_id is None:
        abort(404)
    return jsonify({'unit_id': unit_id})


@app.route('/gallery')
def gallery_page():
    """The journey, oldest first and scrubbable.

    Every other screen answers a question about now — what is owned, what is
    short, what is half-painted. This one is the only backward-looking thing in
    the app, and it is the reward for a log that has been kept: the pile of
    grey plastic in March and the same squad based in August, in one gesture.
    """
    with _read() as conn:
        entries = journey.events(conn)
        return render_template('gallery.html', entries=entries,
                               shots=journey.pictures(conn),
                               span=journey.span(entries))


@app.route('/photos/<path:filename>')
def serve_photo(filename):
    """Behind the login like every other page — these are Clay's models.

    Not served from `static/`: that directory is the app's own assets and ships
    in the image, while these are data and live on the volume beside the
    database.
    """
    path = photos.path_for(filename)
    if not path:
        abort(404)
    return send_file(path, conditional=True)


# ── Painting session ─────────────────────────────────────

@app.route('/paint')
@app.route('/paint/<int:unit_id>')
def paint(unit_id=None):
    """Big tap targets, no forms, no navigation — for use with wet brushes."""
    with _read() as conn:
        unit = breakdown = None
        if unit_id:
            unit = col.get_unit(conn, unit_id)
            if not unit:
                abort(404)
            breakdown = col.unit_breakdown(conn, unit_id)
        return render_template('paint.html', unit=unit, breakdown=breakdown,
                               units=col.paintable_units(conn),
                               stages=col.stage_ladder(conn))


@app.route('/add')
def add_page():
    """Paste a shelf in. The door for everything with no barcode left to scan.

    Scanning covers boxes. It covers nothing already built, painted or split
    out of a box years ago — and those are the models most likely to be missing
    from the app, because recorded one form at a time they never get recorded.
    """
    with _read() as conn:
        return render_template(
            'add.html', stages=col.stage_ladder(conn),
            armies=[a for a in col.list_armies(conn) if a['id']])


@app.route('/add/preview', methods=['POST'])
def add_preview():
    """Every pasted line, matched or not, for confirmation before anything is
    written. Nothing is guessed and nothing is dropped."""
    text = request.form.get('text') or ''
    system = request.form.get('game_system') or None
    with _read() as conn:
        rows = bulk_add.match_lines(conn, bulk_add.parse_lines(text),
                                    game_system=system)
        return render_template(
            'add_preview.html', rows=rows, text=text, game_system=system,
            stages=col.stage_ladder(conn),
            stage_words=sorted(set(bulk_add.STAGE_WORDS)),
            army_id=_int(request.form.get('army_id')),
            stage_id=_int(request.form.get('stage_id')),
            armies=[a for a in col.list_armies(conn) if a['id']],
            unresolved=sum(1 for r in rows if not r['datasheet_id']))


@app.route('/api/add/commit', methods=['POST'])
def api_add_commit():
    data = _payload()
    try:
        with _write() as conn:
            created = bulk_add.commit(
                conn, data.get('rows') or [],
                default_stage_id=_int(data.get('stage_id')),
                army_id=_int(data.get('army_id')))
            return jsonify({'units': created})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


# ── List import (spec §2.7) ──────────────────────────────
#
# The last step of the loop, and it was recorded as blocked on a source for
# longer than it deserved. That was true of *fetching* a list from the web —
# every candidate host is refused by egress policy — but it was never true of
# pasting one, and pasting is the door that always works. A list arrives as
# text far more often than as a file: out of a chat message, a forum post, a
# photo of a printed sheet retyped, someone's app export.

@app.route('/lists/import')
def import_list_page():
    with _read() as conn:
        return render_template(
            'list_import.html', factions=col.list_factions(conn))


@app.route('/lists/import/preview', methods=['POST'])
def import_list_preview():
    """Every pasted line, matched or not, before anything is written.

    The same per-line confirmation the collection paste uses, for the same
    reason: a silently dropped line is a unit Clay turns up to a game without.
    """
    text = request.form.get('text') or ''
    system = request.form.get('game_system') or 'wh40k'
    faction_id = _int(request.form.get('faction_id'))
    with _read() as conn:
        # `list_parse`, not `bulk_add.parse_lines`. The collection paste reads a
        # shelf typed from memory and may skip a line; this reads an app's
        # export, carries points and position, and may never skip anything.
        # Pointing this door at the weaker parser is why pasting a real export
        # here used to report its preamble as four unknown units.
        parsed = list_parse.parse(text)
        rows = [r._asdict() for r in list_resolve.resolve_entries(
            conn, parsed.entries, faction_id=faction_id, game_system=system)]
        return render_template(
            'list_import_preview.html', rows=rows, text=text,
            game_system=system, parsed=parsed,
            name=(request.form.get('name') or '').strip(),
            points_limit=_int(request.form.get('points_limit')),
            faction_id=faction_id,
            factions=col.list_factions(conn),
            unresolved=sum(1 for r in rows if not r['datasheet_id']))


@app.route('/api/lists/import', methods=['POST'])
def api_import_list():
    data = _payload()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'The list needs a name'}), 400
    try:
        with _write() as conn:
            result = army_lists.import_list(
                conn, data.get('rows') or [], name,
                raw_text=data.get('raw_text') or None,
                source_format=data.get('source_format') or None,
                points_total=_int(data.get('points_total')),
                faction_id=_int(data.get('faction_id')),
                points_limit=_int(data.get('points_limit')),
                detachment=(data.get('detachment') or '').strip() or None)
            return jsonify(result), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


# ── Kit templates ────────────────────────────────────────
#
# What is inside a box, defined by hand. This was onboarding's back half —
# scanning found the box, a template said what was in it — and with scanning
# gone it is the whole of it: Clay looks the contents up at the till and types
# them, which he measured as faster than pointing a camera at a shelf.

@app.route('/api/templates/<int:template_id>/want', methods=['POST'])
def api_want_template(template_id):
    """Put a box's contents on the wishlist, remembering the box."""
    try:
        with _write() as conn:
            added = army_lists.want_template(conn, template_id)
            return jsonify({'added': added})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/templates/<int:template_id>/want', methods=['DELETE'])
def api_unwant_template(template_id):
    with _write() as conn:
        return jsonify({'removed': army_lists.unwant_template(conn, template_id)})


@app.route('/api/templates/<int:template_id>/own', methods=['POST'])
def api_own_template(template_id):
    """"I have this one" — the box and every model in it, in one action.

    The same call the scanner makes when it recognises a barcode. Reached from
    the catalogue for a box Clay owns but never scanned.
    """
    data = _payload()
    try:
        with _write() as conn:
            kit_id, unit_ids = col.instantiate_template(
                conn, template_id,
                stage_id=_int(data.get('stage_id')),
                box_state=(data.get('box_state') or 'opened'))
            return jsonify({'kit': kit_id, 'units': unit_ids}), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/templates')
def templates_page():
    with _read() as conn:
        return render_template('templates.html',
                               templates=templates.list_templates(
                                   conn, request.args.get('q')),
                               factions=col.list_factions(conn),
                               query=request.args.get('q') or '')


@app.route('/templates/<int:template_id>')
def template_detail(template_id):
    with _read() as conn:
        template = templates.get_template(conn, template_id)
        if not template:
            abort(404)
        return render_template('template.html', template=template,
                               factions=col.list_factions(conn))


def _contents_from(data):
    """Accept contents as a JSON list, from either a form post or fetch()."""
    contents = data.get('contents')
    if isinstance(contents, str):
        try:
            contents = json.loads(contents)
        except ValueError:
            contents = []
    return contents or []


@app.route('/api/templates/search')
def api_search_templates():
    """Sets matching a typed name, for the add-a-set screen.

    Reuses the catalogue's own query, so searching "Boyz" finds Combat Patrol:
    Orks by what is inside it and not only by what it is called.
    """
    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return jsonify({'results': []})
    with _read() as conn:
        rows = templates.list_templates(conn, query, with_contents=True)[:20]
    return jsonify({'results': [{
        'id': r['id'], 'name': r['name'], 'year': r['year'],
        'faction_name': r['faction_name'], 'model_count': r['model_count'],
        'owned_count': r['owned_count'], 'barcode_count': r['barcode_count'],
        'contents': [f"{c['model_count']}× {c['datasheet_name']}"
                     for c in r['contents']],
    } for r in rows]})


@app.route('/api/templates', methods=['POST'])
def api_create_template():
    data = _payload()
    rrp = data.get('rrp')
    try:
        with _write() as conn:
            template_id = templates.create_template(
                conn, data.get('name') or '', _contents_from(data),
                faction_id=_int(data.get('faction_id')),
                year=_int(data.get('year')),
                rrp_cents=round(float(rrp) * 100) if rrp else None,
                notes=(data.get('notes') or '').strip() or None)
            return jsonify({'id': template_id}), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/templates/<int:template_id>', methods=['PATCH'])
def api_update_template(template_id):
    data = _payload()
    rrp = data.get('rrp')
    contents = _contents_from(data) if 'contents' in data else None
    try:
        with _write() as conn:
            templates.update_template(
                conn, template_id, name=data.get('name'),
                faction_id=_int(data.get('faction_id')),
                year=_int(data.get('year')),
                rrp_cents=round(float(rrp) * 100) if rrp else None,
                notes=data.get('notes'), contents=contents)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})


# ── Pickers ──────────────────────────────────────────────

@app.route('/api/export/inventory')
def api_export_inventory():
    """What Clay owns, for a program rather than a screen.

    Bearer token or a live session — the session so it stays clickable in a
    browser while developing, which is how the shape gets checked without
    writing a client first.

    Read-only by construction: it is a GET that touches nothing.
    """
    army_id = _int(request.args.get('army_id'))
    include_unassigned = _flag(request.args.get('include_unassigned'), False)
    include_capability = _flag(request.args.get('include_capability'), True)
    fmt = (request.args.get('format') or 'json').lower()
    if fmt not in ('json', 'csv'):
        return jsonify({'error': "format must be 'json' or 'csv'"}), 400

    # Validated before the query rather than after: a typo should cost a 400,
    # not a full inventory aggregation thrown away.
    fields = None
    if request.args.get('fields') is not None:
        fields, problem = _export_fields(request.args['fields'],
                                         include_capability)
        if problem:
            return jsonify({'error': problem}), 400
        nested = [f for f in fields if f in _EXPORT_NESTED]
        if fmt == 'csv' and nested:
            return jsonify({'error':
                            f'{", ".join(nested)} cannot be a CSV column — '
                            'use format=json'}), 400

    wanted_faction = (request.args.get('faction') or '').strip()

    with _read() as conn:
        if army_id and not conn.execute('SELECT 1 FROM armies WHERE id = ?',
                                        (army_id,)).fetchone():
            return jsonify({'error': f'no army {army_id}'}), 404
        faction = None
        if wanted_faction:
            # By name or slug rather than id: this gets typed into a curl, and
            # `?faction=orks` is something Clay can write from memory while
            # `?faction_id=1` is a lookup first. 404 like a missing army,
            # because filtering to a faction that does not exist and getting a
            # cheerful empty list is how you conclude you own no Orks.
            faction = conn.execute(
                'SELECT name FROM factions WHERE name = ? COLLATE NOCASE '
                'OR slug = ? COLLATE NOCASE', (wanted_faction,
                                               wanted_faction)).fetchone()
            if not faction:
                return jsonify(
                    {'error': f'no faction {wanted_faction!r}'}), 404
        data = col.export_inventory(conn, army_id=army_id,
                                    include_unassigned=include_unassigned,
                                    include_capability=include_capability)

    if faction:
        # Filtered on the assembled rows rather than in the query, because
        # four things contribute rows — the ownership aggregate, by_stage, the
        # capability join and the flexible join — and the last two can add a
        # datasheet nothing is built as yet. One filter at the end cannot miss
        # a contributor the way four copies of a WHERE clause could.
        data['datasheets'] = [r for r in data['datasheets']
                              if r['faction'] == faction['name']]
    if fields:
        # The envelope is left alone. `fields` narrows the rows; it does not
        # turn the response into a different shape, so a consumer can add it
        # to a URL without rewriting how it reads the reply.
        data['datasheets'] = [{k: row[k] for k in fields if k in row}
                              for row in data['datasheets']]
    if fmt == 'csv':
        return Response(_export_csv(data, fields), mimetype='text/csv',
                        headers={'Content-Disposition':
                                 'attachment; filename="inventory.csv"'})
    return jsonify(data)


def _summary_only(gap):
    """The report without the per-model assignment detail.

    The rows carry every model they were handed so the swappable ones can be
    expanded on screen; a JSON response after a picker only needs the numbers,
    and shipping a few hundred model ids to move one badge is waste.
    """
    return {k: v for k, v in gap.items() if k != 'entries'}


def _assigned_models(conn, gap):
    """For each entry, which models covered it and what they are right now.

    "Swappable rows expand to show which models and what they're built as."
    One query for the whole report rather than one per row.
    """
    ids = [m['id'] for e in gap['entries'] for m in e['assigned']]
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    rows = {r['id']: dict(r) for r in conn.execute(f"""
        SELECT m.id, m.is_flexible, d.name AS built_as, s.name AS stage,
               u.id AS unit_id, k.name AS kit_name
          FROM models m
          JOIN units u ON u.id = m.unit_id
          JOIN stages s ON s.id = m.stage_id
          LEFT JOIN datasheets d ON d.id = m.datasheet_id
          LEFT JOIN kits k ON k.id = u.kit_id
         WHERE m.id IN ({marks})
    """, ids)}
    return {e['id']: [rows[m['id']] for m in e['assigned'] if m['id'] in rows]
            for e in gap['entries']}


def _flag(value, default):
    if value is None or value == '':
        return default
    return value.lower() not in ('0', 'false', 'no', 'off')


#: Fields whose value is a list or a dict. Fine in JSON, meaningless in a CSV
#: cell — `_export_csv` has always dropped them for that reason, and asking for
#: one as a column is refused rather than silently omitted.
_EXPORT_NESTED = ('by_stage', 'points')


def _export_fields(raw, include_capability):
    """Which columns the caller asked for, in their order. Returns (fields, error).

    `fields=name,owned,battle_ready` exists because the full row is built for a
    list optimiser — join keys, every points tier, the whole stage breakdown —
    and the question actually asked at a phone on the sofa is "what do I have
    and how much of it is finished". That was a curl piped through python; now
    it is a URL.

    Unknown names are refused rather than dropped. A typo that silently returns
    fewer columns is a spreadsheet with a column missing and nothing saying
    why, which is the same failure as a silently dropped import line.

    Order is the caller's, because it is the CSV's column order. It does not
    survive into JSON — Flask sorts object keys — and that is fine: nothing
    reading JSON cares, and nothing should depend on key order there.
    """
    names, seen = [], set()
    for name in (n.strip() for n in raw.split(',')):
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        return None, 'fields was empty'

    available = [f for f in col.EXPORT_FIELDS
                 if include_capability or f != 'buildable_from_spare']
    # Named separately so "you turned it off" does not read as "no such field".
    if not include_capability and 'buildable_from_spare' in names:
        return None, ('buildable_from_spare is not computed when '
                      'include_capability=0')
    unknown = [n for n in names if n not in available]
    if unknown:
        return None, (f'unknown field{"s" if len(unknown) > 1 else ""}: '
                      f'{", ".join(unknown)} — choose from '
                      f'{", ".join(available)}')
    return names, None


def _export_csv(data, fields=None):
    """The same rows, flattened.

    `by_stage` and `points` are dropped rather than squashed into a cell: a
    nested structure encoded inside CSV is a thing every consumer parses
    slightly differently, and the JSON form is right there for anyone who
    needs it. The scalars that answer the common questions all survive.
    """
    buffer = io.StringIO()
    if fields:
        # Nested values have no honest CSV form, so asking for one in CSV is a
        # 400 at the route rather than a cell nobody can parse.
        columns = list(fields)
    else:
        columns = ['bsdata_id', 'name', 'faction', 'game_system', 'min_models',
                   'max_models', 'effort', 'owned', 'assembled', 'battle_ready',
                   'wishlist', 'flexible']
        if data['datasheets'] and 'buildable_from_spare' in data['datasheets'][0]:
            columns.append('buildable_from_spare')
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in data['datasheets']:
        writer.writerow(row)
    return buffer.getvalue()


@app.route('/api/datasheets')
def api_datasheets():
    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return jsonify({'results': []})
    with _read() as conn:
        return jsonify({'results': col.search_datasheets(conn, query)})


if __name__ == '__main__':
    port = int(os.getenv('PORT', '3100'))
    log.info('Warhammer Collection Tracker v%s on :%s', VERSION, port)
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG') == '1')
