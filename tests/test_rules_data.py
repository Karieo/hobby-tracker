"""Which revision of the rules data this app is running on.

A list priced from a superseded Munitorum Field Manual is wrong in the one way
Clay would not notice: the numbers still add up, they are just last month's.
Until this existed the only way to check was a shell, and nothing anywhere said
when the pin had aged.
"""

import os
import subprocess
from datetime import date

import pytest

import database as db
import rules_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def priced(conn):
    """A database priced from a known manual."""
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    boyz = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)',
        ('boyz', 'Boyz', orks, db.now(), db.now())).lastrowid
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
        'tier_min, effective_from, source_note) '
        "VALUES (?, 10, 90, 1, '2026-08-05', 'MFM v1.2 (2026-08-05)')",
        (boyz,))
    return {'boyz': boyz}


# ── One pin, one place ───────────────────────────────────────────────────────

def test_the_fetch_scripts_read_the_shared_pins():
    """They declared their own until now. A pin recorded in two places is a
    pin that will eventually disagree with itself, and the disagreement is
    silent — one script fetches one revision while another believes another."""
    import scripts.fetch_bsdata as fetch_bsdata
    import scripts.fetch_killteam as fetch_killteam
    assert fetch_bsdata.BSDATA_SHA == rules_data.BSDATA_SHA
    assert fetch_killteam.KILLTEAM_SHA == rules_data.KILLTEAM_SHA


def test_the_vendored_mfm_pin_matches_what_sources_says():
    """`data/mfm/` is committed, so its pin has no fetch script to live in and
    was only ever a line of prose. Both now exist; this is what keeps them the
    same line."""
    with open(os.path.join(ROOT, 'data', 'SOURCES.md'), encoding='utf-8') as fh:
        assert rules_data.MFM_SHA in fh.read()


def test_every_pin_is_a_full_commit_sha():
    """An abbreviated one is ambiguous forever after the repository grows."""
    for source in rules_data.SOURCES:
        assert len(source['sha']) == 40, source['label']
        assert all(c in '0123456789abcdef' for c in source['sha'])


# ── What is on disk, and what got imported ───────────────────────────────────

def test_the_mfm_files_report_their_own_version():
    meta = rules_data.mfm_meta()
    assert meta.get('version'), 'data/mfm/meta.yaml should name a version'
    assert meta.get('lastUpdated')


def test_a_missing_mfm_directory_is_not_a_crash(monkeypatch, tmp_path):
    """A fresh checkout before the first fetch is a normal state, and
    `/reference` reads this on every page load."""
    monkeypatch.setattr(rules_data, 'MFM_DIR', str(tmp_path / 'nothing'))
    assert rules_data.mfm_meta() == {}


def test_provenance_reports_what_priced_the_database(conn, priced):
    state = rules_data.provenance(conn)
    assert state['points'] == 1
    assert state['points_effective_from'] == '2026-08-05'
    assert 'MFM v1.2' in state['points_source']
    assert state['datasheets'] == 1 and state['killteam'] == 0


def test_kill_team_operatives_are_counted_apart_from_datasheets(conn, priced):
    """Two importers, two sources. Folding them into one number would make
    "did the Kill Team import run?" unanswerable."""
    conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, effort, game_system, '
        'created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)',
        ('kt-boy', 'Ork Boy', 'killteam', db.now(), db.now()))
    state = rules_data.provenance(conn)
    assert state['datasheets'] == 1 and state['killteam'] == 1


def test_hand_overrides_are_counted_so_a_re_import_can_be_trusted(conn, priced):
    """`manual_override` exists so a re-sync does not stamp on a correction.
    Saying how many there are is what makes re-importing a decision rather than
    a leap."""
    conn.execute('UPDATE datasheet_points SET manual_override = 1')
    assert rules_data.provenance(conn)['overrides'] == 1


# ── The pending-import warning ───────────────────────────────────────────────

def test_files_newer_than_the_database_read_as_an_import_pending(
        conn, priced, monkeypatch):
    """Someone updated `data/mfm/` and never re-ran the importer. Every points
    figure in the app is the older manual's, and nothing else would say so."""
    monkeypatch.setattr(rules_data, 'mfm_meta',
                        lambda: {'version': '1.3', 'lastUpdated': '2026-09-02'})
    state = rules_data.provenance(conn)
    assert state['import_pending'] is True
    assert state['mfm_on_disk'] == '2026-09-02'


def test_files_matching_the_database_are_not_a_warning(conn, priced, monkeypatch):
    monkeypatch.setattr(rules_data, 'mfm_meta',
                        lambda: {'version': '1.2', 'lastUpdated': '2026-08-05'})
    assert rules_data.provenance(conn)['import_pending'] is False


def test_an_unpriced_database_is_not_a_warning(conn, monkeypatch):
    """Before the first import there is nothing to be out of date with."""
    monkeypatch.setattr(rules_data, 'mfm_meta',
                        lambda: {'version': '1.3', 'lastUpdated': '2026-09-02'})
    state = rules_data.provenance(conn)
    assert state['points'] == 0 and state['import_pending'] is False


# ── Asking upstream ──────────────────────────────────────────────────────────

#: The real function, captured before `_no_network` shadows it. The three tests
#: below exercise the parser itself and must not get the stub — the first draft
#: of them did, and failed for that reason rather than for a real one.
_REAL_MFM_UPSTREAM = rules_data.mfm_upstream


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """`check_pins` reaches upstream for the MFM dataset now. Nothing in this
    file should touch the network — a suite that does is a suite that fails on
    a train."""
    monkeypatch.setattr(rules_data, 'mfm_upstream',
                        lambda timeout=30, url=None: None)


def offer(monkeypatch, version, day):
    monkeypatch.setattr(
        rules_data, 'mfm_upstream',
        lambda timeout=30, url=None: {'version': version,
                                      'date': date.fromisoformat(day)})


def test_a_moved_pin_is_reported_as_moved(monkeypatch):
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: 'f' * 40)
    rows = rules_data.check_pins()
    assert rows and all(r['moved'] and r['reachable'] for r in rows)


def test_a_pin_still_at_its_head_is_not_reported(monkeypatch):
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: rules_data.MFM_SHA)
    row = next(r for r in rules_data.check_pins() if r['key'] == 'mfm')
    assert row['moved'] is False and row['reachable'] is True


def test_an_unreachable_github_is_reported_rather_than_raised(monkeypatch):
    """The weekly sweep runs unattended. A network blip must not end it, and
    "could not check" is a different answer from "nothing moved"."""
    monkeypatch.setattr(rules_data, 'remote_head', lambda repo, timeout=60: None)
    rows = rules_data.check_pins()
    assert all(r['reachable'] is False and r['moved'] is False for r in rows)


# ── Moved is not the same as stale ───────────────────────────────────────────

def test_a_chore_commit_does_not_report_new_points(monkeypatch):
    """Measured 2026-08-26. The MFM pin had moved and the only commit was
    `chore(deps): Bump pnpm/setup from 1 to 2` — the points files were
    byte-identical. The old check said MOVED and there was nothing to take.

    A weekly warning about something there is nothing to do about is a nag, and
    a nag becomes wallpaper."""
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: 'f' * 40)
    ours = rules_data.mfm_meta().get('lastUpdated')
    offer(monkeypatch, '1.2', ours.isoformat())

    row = next(r for r in rules_data.check_pins() if r['key'] == 'mfm')

    assert row['moved'] is True, 'the repository did move'
    assert row['stale'] is False, 'but the points did not'


def test_a_newer_dataset_is_reported_as_stale(monkeypatch):
    """The case that actually matters: GW repriced and the scrape picked it
    up."""
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: 'f' * 40)
    offer(monkeypatch, '1.3', '2099-01-01')

    row = next(r for r in rules_data.check_pins() if r['key'] == 'mfm')

    assert row['stale'] is True
    assert row['dataset'] == {'version': '1.3',
                              'date': date.fromisoformat('2099-01-01')}


def test_staleness_is_None_where_it_cannot_be_established(monkeypatch):
    """BSData and Kill Team publish no dated dataset, so "not established" is
    the honest answer. Reporting False would claim they are current on evidence
    nobody has."""
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: 'f' * 40)

    rows = {r['key']: r for r in rules_data.check_pins()}

    assert rows['bsdata']['stale'] is None
    assert rows['killteam']['stale'] is None


def test_an_unreachable_changelog_does_not_claim_currency(monkeypatch):
    monkeypatch.setattr(rules_data, 'remote_head',
                        lambda repo, timeout=60: rules_data.MFM_SHA)
    monkeypatch.setattr(rules_data, 'mfm_upstream',
                        lambda timeout=30, url=None: None)

    row = next(r for r in rules_data.check_pins() if r['key'] == 'mfm')

    assert row['stale'] is None and row['dataset'] is None


def test_mfm_upstream_parses_the_newest_entry(monkeypatch, tmp_path):
    """The newest heading wins, and the format is upstream's own."""
    body = ('# Data changelog\n\n'
            '## [2026-08-05] \u2014 MFM v1.2\n\n### Added\n- a thing\n\n'
            '## [2026-07-22] \u2014 MFM v1.1\n')
    path = tmp_path / 'CHANGELOG.md'
    path.write_text(body, encoding='utf-8')

    got = _REAL_MFM_UPSTREAM(url=path.as_uri())

    assert got == {'version': '1.2', 'date': date.fromisoformat('2026-08-05')}


def test_a_changelog_it_cannot_parse_reads_as_unknown(monkeypatch, tmp_path):
    """An upstream that changed its format is a thing to report as unknown, not
    to crash the sweep over."""
    path = tmp_path / 'CHANGELOG.md'
    path.write_text('# Data changelog\n\nnothing parseable here\n',
                    encoding='utf-8')

    assert _REAL_MFM_UPSTREAM(url=path.as_uri()) is None


def test_an_unreachable_changelog_never_raises():
    assert _REAL_MFM_UPSTREAM(
        url='https://example.invalid/nope.md', timeout=2) is None


def test_remote_head_swallows_a_failing_git(monkeypatch):
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired('git', 1)
    monkeypatch.setattr(rules_data.subprocess, 'run', boom)
    assert rules_data.remote_head('https://example.invalid/x') is None


def test_the_checker_never_bumps_a_pin():
    """"Points moving under a list is something to accept deliberately, not to
    wake up to." The script reports and exits non-zero; it writes nothing."""
    with open(os.path.join(ROOT, 'scripts', 'check_rules_pins.py'),
              encoding='utf-8') as fh:
        source = fh.read()
    for forbidden in ('open(', 'write(', 'UPDATE ', 'INSERT '):
        assert forbidden not in source, \
            f'the pin checker must not {forbidden.strip()} anything'
