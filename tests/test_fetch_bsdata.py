"""The fetch script's refusal path, which is the one that gives advice.

It cannot delete anything, so its failure mode is telling a user to. That
happened on the live deploy: a complete 46-catalogue checkout that container
git would not read (created on the host by another uid) was reported as "not
a BSData checkout. Move it aside" — advice that would have thrown away good
data. These pin the two cases apart.
"""

import subprocess
import sys

import pytest

sys.path.insert(0, 'scripts')
import fetch_bsdata as fb


@pytest.fixture
def dest(tmp_path, monkeypatch):
    d = tmp_path / 'bsdata'
    monkeypatch.setattr(fb, 'DEST', str(d))
    return d


def test_every_git_call_is_scoped_with_safe_directory():
    # Without this the container, running git as root over a host-owned
    # checkout, gets "detected dubious ownership" on a directory that is fine.
    cmd = fb.git('-C', '/x', 'rev-parse', 'HEAD')
    assert cmd[:3] == ['git', '-c', f'safe.directory={fb.DEST}']


def test_current_sha_returns_the_error_rather_than_swallowing_it(dest):
    dest.mkdir()
    sha, err = fb.current_sha(str(dest))
    assert sha is None
    assert err, 'the caller cannot give correct advice without git\'s reason'


def test_current_sha_reads_a_real_checkout(dest):
    dest.mkdir()
    for cmd in (['init', '--quiet'], ['commit', '--allow-empty', '-qm', 'x']):
        subprocess.run(['git', '-C', str(dest)] + cmd, check=True,
                       env={'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
                            'PATH': '/usr/bin:/bin'})
    sha, err = fb.current_sha(str(dest))
    assert err is None
    assert len(sha) == 40


def test_catalogues_present_is_never_told_to_move_it_aside(dest, capsys):
    # The live failure. Data intact, git unable to read it.
    dest.mkdir()
    for name in ('Aeldari - Craftworlds.json', 'Chaos - Chaos Daemons.json'):
        (dest / name).write_text('{}')

    assert fb.main([]) == 1
    err = capsys.readouterr().err
    assert 'Move it aside' not in err, 'that advice destroys a good checkout'
    assert 'do not move or delete it' in err
    assert 'import_bsdata.py' in err, 'the importer needs no git — say so'
    assert '2 catalogues' in err


def test_an_empty_obstruction_is_still_told_to_move_aside(dest, capsys):
    dest.mkdir()
    (dest / 'stray.txt').write_text('x')

    assert fb.main([]) == 1
    err = capsys.readouterr().err
    assert 'Move it aside' in err
