"""Warhammer Collection Tracker — Flask server.

Build steps 1-4: reference data, the collection and stage pipeline, and the
scanner with its sprint queue and review screen. Routes live here and delegate
to ``collection.py`` and ``scanning.py``; the collection search view (step 5) is
not built yet.

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
from datetime import timedelta
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
                   render_template, request, session)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import bulk_add  # noqa: E402
import collection as col
import list_allocate
import list_parse
import list_resolve
import lists as army_lists
import rules_data  # noqa: E402
import database as db  # noqa: E402
import scanning as scan  # noqa: E402

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


def filter_url(**overrides):
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
    query = urlencode(sorted(args.items()))
    return f'{request.path}?{query}' if query else request.path


@app.context_processor
def inject_globals():
    return {'owner': os.getenv('OWNER_NAME', 'Clay'), 'version': VERSION,
            'filter_url': filter_url,
            'asset_version': _ASSET_VERSION, 'currency': CURRENCY,
            'currency_symbol': CURRENCY_SYMBOL,
            # Where this app answers over HTTPS. The scan page needs it: the
            # camera refuses to run outside a secure context, and "use the
            # tunnel address" is a poor answer to give someone holding a box.
            'public_url': (os.getenv('PUBLIC_URL') or '').strip().rstrip('/')}


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

    query = (request.args.get('q') or '').strip()
    # Three states rather than the old "unowned appear only when searching":
    # mine (the inventory), wanted (the shopping list), everything (the
    # catalogue, which is what the own-it check needs). Searching still opens
    # it up to everything by default, so the shop question is one box as
    # before — but now it can be said rather than inferred.
    own = request.args.get('own') or ('all' if query else 'mine')
    sort = request.args.get('sort') or 'name'
    with _read() as conn:
        rows = col.inventory(
            conn, query=query or None,
            faction_id=_int(request.args.get('faction_id')),
            game_system=(request.args.get('system') or None),
            stage_id=_int(request.args.get('stage_id')),
            points_min=_int(request.args.get('points_min')),
            points_max=_int(request.args.get('points_max')),
            only_wanted=(own == 'wanted'),
            sort=sort,
            include_unowned=(own == 'all'))
        # Chip filters narrow what is already loaded rather than re-querying:
        # "unpainted" and "sealed" are questions about the rows on screen, and
        # keeping them here means the chips cannot disagree with the counts.
        chip = request.args.get('filter') or ''
        if chip == 'unpainted':
            rows = [r for r in rows if r['done_count'] < r['owned_count']]
        elif chip == 'sealed':
            rows = [r for r in rows if r['sealed_boxes']]
        return render_template(
            'collection.html', rows=rows, query=query, filter=chip,
            system=(request.args.get('system') or ''),
            faction_id=_int(request.args.get('faction_id')),
            stage_id=_int(request.args.get('stage_id')),
            points_min=request.args.get('points_min') or '',
            points_max=request.args.get('points_max') or '',
            own=own, sort=sort, sorts=col.INVENTORY_SORT_LABELS,
            factions=col.list_factions(conn),
            stages=col.stage_ladder(conn),
            totals={
                'datasheets': len(rows),
                'owned': sum(r['owned_count'] for r in rows),
                'built': sum(r['built_count'] for r in rows),
                'done': sum(r['done_count'] for r in rows),
                'wanted': sum(r['wanted_count'] for r in rows),
                'sealed': sum(r['sealed_boxes'] for r in rows),
            })


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
    a kit disposal, which keeps them.
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


# ── Kits ─────────────────────────────────────────────────

@app.route('/kits')
def kits_page():
    with _read() as conn:
        return render_template('kits.html', kits=col.list_kits(conn),
                               factions=col.list_factions(conn),
                               armies=[a for a in col.list_armies(conn) if a['id']])


@app.route('/api/kits', methods=['POST'])
def api_create_kit():
    data = _payload()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'A kit needs a name'}), 400
    cost = data.get('cost')
    with _write() as conn:
        kit_id = col.create_kit(
            conn, name, faction_id=_int(data.get('faction_id')),
            source=(data.get('source') or None),
            source_ref=(data.get('source_ref') or '').strip() or None,
            acquired_on=(data.get('acquired_on') or '').strip() or None,
            cost_cents=round(float(cost) * 100) if cost else None,
            box_state=(data.get('box_state') or 'sealed'),
            notes=(data.get('notes') or '').strip() or None)
    return jsonify({'id': kit_id}), 201


@app.route('/kits/<int:kit_id>')
def kit_page(kit_id):
    """One box: what it is, what is in it, and what can be done about it."""
    with _read() as conn:
        kit = col.get_kit(conn, kit_id)
        if not kit:
            abort(404)
        return render_template(
            'kit.html', kit=kit,
            units=col.list_units(conn, kit_id=kit_id),
            stages=col.stage_ladder(conn),
            factions=col.list_factions(conn),
            armies=[a for a in col.list_armies(conn) if a['id']],
            templates=col.list_templates_with_contents(conn))


@app.route('/api/kits/<int:kit_id>', methods=['POST'])
def api_update_kit(kit_id):
    data = _payload()
    fields = {}
    for key in ('name', 'source', 'source_ref', 'acquired_on', 'notes'):
        if key in data:
            fields[key] = (data.get(key) or '').strip() or None
    if 'name' in fields and not fields['name']:
        return jsonify({'error': 'A kit needs a name'}), 400
    if 'box_state' in data:
        fields['box_state'] = data['box_state']
    if 'faction_id' in data:
        fields['faction_id'] = _int(data.get('faction_id'))
    if 'cost' in data:
        cost = data.get('cost')
        fields['cost_cents'] = round(float(cost) * 100) if cost else None
    try:
        with _write() as conn:
            col.update_kit(conn, kit_id, **fields)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})


@app.route('/api/kits/<int:kit_id>', methods=['DELETE'])
def api_delete_kit(kit_id):
    """A mis-scan or a duplicate. Selling one is a status change, not this."""
    try:
        with _write() as conn:
            col.delete_kit(conn, kit_id)
    except ValueError:
        abort(404)
    return jsonify({'success': True})


@app.route('/api/kits/<int:kit_id>/status', methods=['POST'])
def api_kit_status(kit_id):
    data = _payload()
    price = data.get('price')
    with _write() as conn:
        if not col.get_kit(conn, kit_id):
            abort(404)
        try:
            col.dispose_kit(conn, kit_id, (data.get('status') or '').strip(),
                            disposed_on=(data.get('disposed_on') or '').strip() or None,
                            price_cents=round(float(price) * 100) if price else None,
                            note=(data.get('note') or '').strip() or None)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})


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


# ── Scanning: capture ────────────────────────────────────

@app.route('/scan')
def scan_page():
    """Sprint capture. The camera stays open and nothing interrupts it.

    Needs a secure context for getUserMedia — the Cloudflare Tunnel supplies
    that, a plain-http Tailscale IP does not, and the page says so rather than
    failing silently.
    """
    with _read() as conn:
        return render_template('scan.html', summary=scan.queue_summary(conn),
                               recent=scan.queue_rows(conn)[:8])


@app.route('/api/scan', methods=['POST'])
def api_scan():
    """One decode. Written to the server at once — a dead battery must not cost
    Clay the shelf he just worked through."""
    data = _payload()
    try:
        with _write() as conn:
            result = scan.enqueue_scan(conn, data.get('code') or '')
            result['summary'] = scan.queue_summary(conn)
            return jsonify(result), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/scan/check')
def api_scan_check():
    """Sanity notes for a typed code, before it is committed to the queue."""
    code = scan.normalise_code(request.args.get('code') or '')
    with _read() as conn:
        template = scan.template_for_code(conn, code) if code else None
    return jsonify({**scan.describe_code(code), 'known': template is not None,
                    'name': template['name'] if template else None})


# ── Scanning: enrichment ─────────────────────────────────

@app.route('/scan/review')
def scan_review():
    """The keyboard half, done later. Known codes need a tap; unknown ones need
    contents defined once each."""
    with _read() as conn:
        return render_template(
            'scan_review.html',
            rows=scan.queue_rows(conn, include_resolved=True),
            summary=scan.queue_summary(conn),
            stages=col.stage_ladder(conn),
            armies=[a for a in col.list_armies(conn) if a['id']],
            factions=col.list_factions(conn),
            awaiting=col.kits_awaiting_contents(conn),
            templates=col.list_templates_with_contents(conn))


@app.route('/api/scan/<int:queue_id>/resolve', methods=['POST'])
def api_resolve_scan(queue_id):
    data = _payload()
    cost = data.get('cost')
    try:
        with _write() as conn:
            kit_ids = scan.resolve_queue_row(
                conn, queue_id,
                army_id=_int(data.get('army_id')),
                stage_id=_int(data.get('stage_id')),
                source=(data.get('source') or None),
                acquired_on=(data.get('acquired_on') or '').strip() or None,
                cost_cents=round(float(cost) * 100) if cost else None,
                box_state=(data.get('box_state') or 'opened'))
            return jsonify({'kits': kit_ids, 'summary': scan.queue_summary(conn)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/scan/<int:queue_id>/shelve', methods=['POST'])
def api_shelve_scan(queue_id):
    """Record the box, not its contents. One tap, nothing invented."""
    data = _payload()
    cost = data.get('cost')
    try:
        with _write() as conn:
            kit_ids = scan.shelve_queue_row(
                conn, queue_id,
                name=data.get('name'),
                faction_id=_int(data.get('faction_id')),
                source=(data.get('source') or None),
                acquired_on=(data.get('acquired_on') or '').strip() or None,
                cost_cents=round(float(cost) * 100) if cost else None,
                box_state=(data.get('box_state') or 'sealed'))
            return jsonify({'kits': kit_ids, 'summary': scan.queue_summary(conn)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/kits/<int:kit_id>/adopt', methods=['POST'])
def api_adopt_template(kit_id):
    """Fill in a shelved box's contents once its template exists."""
    data = _payload()
    try:
        with _write() as conn:
            unit_ids = col.adopt_template(
                conn, kit_id, _int(data.get('kit_template_id')),
                army_id=_int(data.get('army_id')),
                stage_id=_int(data.get('stage_id')))
            return jsonify({'units': unit_ids})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/scan/<int:queue_id>/quantity', methods=['POST'])
def api_scan_quantity(queue_id):
    with _write() as conn:
        scan.set_queue_quantity(conn, queue_id, _int(_payload().get('quantity'), 1))
    return jsonify({'success': True})


@app.route('/api/scan/<int:queue_id>', methods=['DELETE'])
def api_discard_scan(queue_id):
    with _write() as conn:
        scan.discard_queue_row(conn, queue_id)
    return jsonify({'success': True})


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


@app.route('/box/<code>')
def box_page(code):
    """Everything known about one barcode, reached by scanning the box itself.

    The couch half of onboarding. A hundred shelved boxes are named
    `Unidentified box 5011921…` and nothing but thirteen digits says which is
    which — so instead of reading digits off a screen, Clay picks the box up,
    scans it, and lands here to say what is in it. The box is its own index.
    """
    code = scan.normalise_code(code)
    with _read() as conn:
        template = scan.template_for_code(conn, code)
        return render_template(
            'box.html', code=code, notes=scan.describe_code(code)['notes'],
            template=scan.get_template(conn, template['id']) if template else None,
            awaiting=[k for k in col.kits_awaiting_contents(conn)
                      if k['code'] == code],
            kits=[dict(r) for r in conn.execute(
                'SELECT k.*, t.name AS template_name FROM kits k '
                'LEFT JOIN kit_templates t ON t.id = k.kit_template_id '
                'WHERE k.source_ref = ? ORDER BY k.id', (code,))],
            open_rows=[r for r in scan.queue_rows(conn) if r['code'] == code],
            stages=col.stage_ladder(conn),
            armies=[a for a in col.list_armies(conn) if a['id']])


@app.route('/api/box/<code>/adopt-all', methods=['POST'])
def api_adopt_all_for_code(code):
    """Define contents once, fill in every copy already on the shelf."""
    data = _payload()
    try:
        with _write() as conn:
            kit_ids = col.adopt_all_for_code(
                conn, scan.normalise_code(code),
                kit_template_id=_int(data.get('kit_template_id')),
                army_id=_int(data.get('army_id')),
                stage_id=_int(data.get('stage_id')))
            return jsonify({'kits': kit_ids})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/scan/sweep', methods=['POST'])
def api_scan_sweep():
    """The whole queue in one tap: known boxes confirmed, unknown ones
    recorded. A hundred rows must not cost a hundred taps."""
    data = _payload()
    cost = data.get('cost')
    try:
        with _write() as conn:
            result = scan.sweep_queue(
                conn,
                army_id=_int(data.get('army_id')),
                stage_id=_int(data.get('stage_id')),
                source=(data.get('source') or None),
                acquired_on=(data.get('acquired_on') or '').strip() or None,
                cost_cents=round(float(cost) * 100) if cost else None,
                box_state=(data.get('box_state') or 'sealed'))
            return jsonify({'confirmed': len(result['confirmed']),
                            'shelved': len(result['shelved']),
                            'summary': scan.queue_summary(conn)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


# ── Kit templates ────────────────────────────────────────
#
# Built by hand first, deliberately: onboarding must never depend on an EAN
# lookup answering or a vision model being right.

@app.route('/sets/new')
def add_set_page():
    """Add a set, three ways in.

    By name is the door: most of the time Clay knows what the box is called,
    and typing it beats hunting for a barcode on a shelf or waiting for a
    camera to focus. Scanning and typing a code stay as the other two doors —
    they are better when the box is in his hand and the name is a mouthful.

    All three end in the same place: a kit, with its contents if the app knows
    them and honestly without if it does not.
    """
    with _read() as conn:
        return render_template(
            'add_set.html',
            recent=scan.list_templates(conn, with_contents=True)[:8],
            factions=col.list_factions(conn))


@app.route('/catalogue')
def catalogue_page():
    """What exists, and whether Clay has it.

    `/templates` is bookkeeping — the boxes he has defined. This asks the
    question a catalogue is for, and is where researched contents stop being a
    dropdown entry and start being useful: browse, see what you own, and put
    what you don't on the wishlist.
    """
    owned = request.args.get('owned')
    with _read() as conn:
        return render_template(
            'catalogue.html',
            templates=scan.list_templates(
                conn, request.args.get('q'),
                faction_id=_int(request.args.get('faction_id')),
                owned=owned if owned in ('yes', 'no') else None,
                with_contents=True),
            factions=col.list_factions(conn),
            query=request.args.get('q') or '',
            owned=owned or '',
            faction_id=_int(request.args.get('faction_id')))


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
                               templates=scan.list_templates(
                                   conn, request.args.get('q')),
                               factions=col.list_factions(conn),
                               query=request.args.get('q') or '')


@app.route('/templates/<int:template_id>')
def template_detail(template_id):
    with _read() as conn:
        template = scan.get_template(conn, template_id)
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
        rows = scan.list_templates(conn, query, with_contents=True)[:20]
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
            template_id = scan.create_template(
                conn, data.get('name') or '', _contents_from(data),
                faction_id=_int(data.get('faction_id')),
                year=_int(data.get('year')),
                rrp_cents=round(float(rrp) * 100) if rrp else None,
                notes=(data.get('notes') or '').strip() or None)
            code = scan.normalise_code(data.get('code') or '')
            if code:
                scan.link_barcode(conn, code, template_id)
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
            scan.update_template(
                conn, template_id, name=data.get('name'),
                faction_id=_int(data.get('faction_id')),
                year=_int(data.get('year')),
                rrp_cents=round(float(rrp) * 100) if rrp else None,
                notes=data.get('notes'), contents=contents)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True})


@app.route('/api/templates/<int:template_id>/barcodes', methods=['POST'])
def api_link_barcode(template_id):
    """Teach the local table what a code is — the step that makes every future
    scan of that box instant."""
    code = scan.normalise_code(_payload().get('code') or '')
    if not code:
        return jsonify({'error': 'No digits in that code'}), 400
    with _write() as conn:
        if not scan.get_template(conn, template_id):
            abort(404)
        scan.link_barcode(conn, code, template_id)
    return jsonify({'success': True, **scan.describe_code(code)}), 201


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

    with _read() as conn:
        if army_id and not conn.execute('SELECT 1 FROM armies WHERE id = ?',
                                        (army_id,)).fetchone():
            return jsonify({'error': f'no army {army_id}'}), 404
        data = col.export_inventory(conn, army_id=army_id,
                                    include_unassigned=include_unassigned,
                                    include_capability=include_capability)
    if fmt == 'csv':
        return Response(_export_csv(data), mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename="inventory.csv"'})
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


def _export_csv(data):
    """The same rows, flattened.

    `by_stage` and `points` are dropped rather than squashed into a cell: a
    nested structure encoded inside CSV is a thing every consumer parses
    slightly differently, and the JSON form is right there for anyone who
    needs it. The scalars that answer the common questions all survive.
    """
    buffer = io.StringIO()
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
