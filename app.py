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

import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
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
from flask import (Flask, abort, jsonify, redirect,  # noqa: E402
                   render_template, request, session)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import collection as col  # noqa: E402
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


@app.route('/reference')
def reference():
    """What the importer loaded, and what it could not resolve."""
    with _read() as conn:
        unresolved = [dict(r) for r in db.open_unresolved(conn)]
        stages = col.stage_ladder(conn)
    return render_template('reference.html', summary=db.import_summary(),
                           stages=stages, unresolved=unresolved)


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


@app.context_processor
def inject_globals():
    return {'owner': os.getenv('OWNER_NAME', 'Clay'), 'version': VERSION,
            'asset_version': _ASSET_VERSION}


# ── Armies ───────────────────────────────────────────────

@app.route('/')
def index():
    with _read() as conn:
        armies = col.list_armies(conn)
        factions = col.list_factions(conn)
        summary = db.import_summary()
    return render_template('armies.html', armies=armies, factions=factions,
                           summary=summary)


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
        return render_template(
            'unit.html', unit=unit,
            breakdown=col.unit_breakdown(conn, unit_id),
            models=col.unit_models(conn, unit_id),
            stages=col.stage_ladder(conn),
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
        unit_id = col.create_unit(
            conn, datasheet_id, model_count,
            army_id=_int(data.get('army_id')),
            kit_id=_int(data.get('kit_id')),
            stage_id=_int(data.get('stage_id')),
            nickname=(data.get('nickname') or '').strip() or None)
    return jsonify({'id': unit_id}), 201


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
        col.update_unit(conn, unit_id, nickname=data.get('nickname'),
                        notes=data.get('notes'))
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


# ── Kit templates ────────────────────────────────────────
#
# Built by hand first, deliberately: onboarding must never depend on an EAN
# lookup answering or a vision model being right.

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
