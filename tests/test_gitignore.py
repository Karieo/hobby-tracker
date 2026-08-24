"""The .env family must stay out of git, and .env.example must stay in.

.gitignore is the only thing between a stray editor backup and published
credentials, and its patterns are easy to get subtly wrong — `.env` does not
match `.env.save`, and a `.env.*` written without the negation takes
`.env.example` out of the repo along with it. These assert the real behaviour
via git rather than the file's text, because the text is not the contract.
"""

import subprocess

import pytest

REPO = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                      capture_output=True, text=True).stdout.strip()


def ignored(path):
    return subprocess.run(['git', 'check-ignore', '-q', path],
                          cwd=REPO).returncode == 0


pytestmark = pytest.mark.skipif(not REPO, reason='not a git checkout')


@pytest.mark.parametrize('name', [
    '.env',
    '.env.save',      # nano, on write
    '.env.save.1',    # nano, second write
    '..env.swp',      # nano, while open
    '.env.swp',
    '.env.bak',
    '.env.local',
])
def test_env_and_its_editor_copies_are_ignored(name):
    assert ignored(name), f'{name} would be committed by `git add -A`'


def test_the_example_is_still_tracked():
    # It ships the keys a deploy needs, with no values. Ignoring it would make
    # deploy.sh's `cp .env.example .env` fail on a fresh clone.
    assert not ignored('.env.example')


def test_photos_are_ignored():
    """They are data on the volume, not source. A `git add -A` after an
    afternoon of photographing a shelf would otherwise commit a few hundred
    megabytes of JPEGs to a repository that has no business holding them."""
    assert ignored('data/photos/deadbeef.jpg')
