"""Which revision of the rules data this app is running on, and whether it has aged.

Three upstream sources, three pins, and until now no way to answer "am I on the
current points?" without a shell. That matters more than it sounds: Games
Workshop reprices with every balance dataslate, and a list priced from a
superseded Munitorum Field Manual is wrong in the one way Clay would not notice
— the numbers still add up, they are just last month's.

Two questions, deliberately answered in different places:

**"What am I running, and is there an import pending?"** is local — the
database against the files on disk — so `/reference` answers it on every page
load without touching the network. An app that needed outbound HTTP to render a
page would be an app that breaks when GitHub does.

**"Has upstream moved past my pin?"** needs the network and is slow, so it
belongs to the weekly sweep, which already runs on a schedule and is already
allowed to take its time. `scripts/check_rules_pins.py` is that half.

THE PINS LIVE HERE, ONCE. `fetch_bsdata.py` and `fetch_killteam.py` import
them rather than declaring their own, because a pin recorded in two places is a
pin that will disagree with itself — and the MFM's had no machine-readable home
at all, only a line of prose in `data/SOURCES.md`. A test asserts the two still
agree.
"""

import os
import re
import subprocess
import urllib.request
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MFM_DIR = os.path.join(BASE_DIR, 'data', 'mfm')

# `data/mfm/` is vendored and committed, so nothing fetches it — this pin is
# the record of which upstream commit those files were taken from, and the only
# way to tell that they have aged.
MFM_REPO = 'https://github.com/BSData/wh40k-11e-mfm'
MFM_SHA = '06754e2f2e0e9c2b3f7fe46b2a96972702f43f22'

BSDATA_REPO = 'https://github.com/BSData/wh40k-11e'
BSDATA_SHA = '13f3c4e54d15f96baebdc48c3a8c10431db2990f'

KILLTEAM_REPO = 'https://github.com/BSData/wh40k-killteam'
KILLTEAM_SHA = 'bdca455a43faf5795563549d24ede776eddfda8c'

SOURCES = (
    {'key': 'mfm', 'label': 'Munitorum points', 'repo': MFM_REPO,
     'sha': MFM_SHA, 'vendored': True},
    {'key': 'bsdata', 'label': 'Datasheets', 'repo': BSDATA_REPO,
     'sha': BSDATA_SHA, 'vendored': False},
    {'key': 'killteam', 'label': 'Kill Team operatives', 'repo': KILLTEAM_REPO,
     'sha': KILLTEAM_SHA, 'vendored': False},
)


def mfm_meta():
    """The version and date of the MFM files sitting in `data/mfm/`.

    Read with the same loader the importer uses. Returns empty rather than
    raising when the directory is missing — a fresh checkout before the first
    fetch is a normal state, not an error to crash a page over.
    """
    path = os.path.join(MFM_DIR, 'meta.yaml')
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path, encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


def imported(conn):
    """What is actually in the database, as opposed to on disk.

    The importer writes `MFM v1.2 (2026-08-05)` into every points row's
    `source_note` and the date into `effective_from`, so the database knows
    which manual priced it — which is the number that matters, because that is
    what the list screens are quoting.
    """
    points = conn.execute("""
        SELECT COUNT(*) AS n, MAX(effective_from) AS effective_from,
               MAX(source_note) AS source_note
          FROM datasheet_points
    """).fetchone()
    sheets = conn.execute("""
        SELECT COUNT(*) AS n, MAX(updated_at) AS updated_at,
               SUM(CASE WHEN game_system = 'killteam' THEN 1 ELSE 0 END) AS killteam
          FROM datasheets
    """).fetchone()
    return {
        'points': points['n'],
        'points_effective_from': points['effective_from'],
        'points_source': points['source_note'],
        'datasheets': (sheets['n'] or 0) - (sheets['killteam'] or 0),
        'killteam': sheets['killteam'] or 0,
        'datasheets_updated_at': sheets['updated_at'],
        'overrides': conn.execute(
            'SELECT COUNT(*) FROM datasheet_points WHERE manual_override = 1'
        ).fetchone()[0],
    }


def provenance(conn):
    """Everything `/reference` needs, without a single network call.

    `import_pending` is the one worth acting on: the files on disk say a newer
    manual than the database was priced from, which means someone updated
    `data/mfm/` and never re-ran the importer. The points on screen are the old
    ones and nothing else would ever say so.
    """
    meta = mfm_meta()
    state = imported(conn)
    on_disk = str(meta.get('lastUpdated') or '') or None
    pending = bool(on_disk and state['points_effective_from']
                   and on_disk != state['points_effective_from'])
    return dict(state,
                mfm_version=meta.get('version'),
                mfm_on_disk=on_disk,
                import_pending=pending,
                sources=[dict(s) for s in SOURCES])


def remote_head(repo, timeout=60):
    """The commit a repository's default branch is on right now.

    `git ls-remote` rather than the GitHub API: no token, no rate limit, and it
    is the same protocol the fetch scripts already use, so if this works the
    fetch will too. Returns None on any failure — an unreachable GitHub is a
    thing to report, never a thing to crash the weekly sweep over.
    """
    try:
        out = subprocess.run(['git', 'ls-remote', repo, 'HEAD'],
                             capture_output=True, text=True, timeout=timeout,
                             check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    parts = out.stdout.split()
    return parts[0] if parts else None


#: Upstream's own record of when the points dataset last changed. Fetched as a
#: raw file rather than through the GitHub API, which needs a token and a rate
#: limit; this needs neither and works from the sweep.
MFM_CHANGELOG_URL = (
    'https://raw.githubusercontent.com/BSData/wh40k-11e-mfm/HEAD/'
    'DATA-CHANGELOG.md')

#: `## [2026-08-05] — MFM v1.2` — the newest heading is the newest dataset.
_MFM_ENTRY = re.compile(r'^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*[—-]\s*MFM\s+v(\S+)',
                        re.MULTILINE)


def mfm_upstream(timeout=30, url=MFM_CHANGELOG_URL):
    """The newest points dataset upstream has published, or None.

    Why this exists at all: comparing commit SHAs answers "has the repository
    moved", which is **not** the question. Measured on 2026-08-26 —

      * the MFM pin had moved, and the one commit was
        `chore(deps): Bump pnpm/setup from 1 to 2`. The points files were
        byte-identical. The check said MOVED and there was nothing to take.
      * the BSData pin had moved by 35 commits, every one a real data fix, and
        re-importing changed **two rows** — both keyword-only, on units in
        armies Clay does not play. BSData's JSON carries the whole BattleScribe
        model; this app reads a narrow slice of it.

    Both directions are wrong in the way that matters: a weekly warning about
    something there is nothing to do about is a nag, and a nag becomes
    wallpaper. So for the one source that publishes a versioned dataset, ask it
    directly.

    Returns ``{'version', 'date'}`` or None when unreachable or unparseable —
    never raises. An upstream that changed its changelog format is a thing to
    report as unknown, not to crash the sweep over.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode('utf-8', 'replace')
    except Exception:                                   # noqa: BLE001
        return None
    match = _MFM_ENTRY.search(body)
    if not match:
        return None
    try:
        return {'date': date.fromisoformat(match.group(1)),
                'version': match.group(2)}
    except ValueError:
        return None


def check_pins(timeout=60, conn=None):
    """Each pinned source against its upstream. Network-bound; sweep-only.

    Two different questions, kept apart because conflating them is what made
    this check cry wolf:

    ``moved``  the repository has commits past the pin. Cheap, and weak — it
               is true for a CI chore and for a balance dataslate alike.
    ``stale``  what this app *imports* is genuinely behind. Only answerable
               for the MFM, which publishes a dated, versioned dataset; None
               for the sources that do not, because "not established" and "no"
               are different answers and only one of them is honest here.
    """
    theirs = mfm_upstream(timeout=timeout)
    ours = mfm_meta().get('lastUpdated')

    results = []
    for source in SOURCES:
        head = remote_head(source['repo'], timeout=timeout)
        row = dict(source, head=head,
                   moved=bool(head) and head != source['sha'],
                   reachable=head is not None,
                   dataset=None, stale=None)
        if source['key'] == 'mfm' and theirs:
            row['dataset'] = theirs
            row['stale'] = bool(ours) and theirs['date'] > ours
        results.append(row)
    return results
