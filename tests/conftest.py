"""Shared fixtures.

The app reads its DB path from ``database.DB_PATH`` at call time, so pointing
that at a temp file before importing anything else is enough to isolate the
suite. Every test gets a freshly migrated database — these tests are about
schema and import behaviour, so a clean slate per test is worth the cost.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMPDIR = tempfile.mkdtemp(prefix='hobby-tracker-test-')
os.environ['SESSION_SECRET'] = 'test-secret'
os.environ.setdefault('OWNER_NAME', 'Clay')
os.environ.setdefault('OWNER_PASSWORD', 'testpw')

import database as db  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    """A migrated, empty database. Also repoints database.DB_PATH at it."""
    path = str(tmp_path / 'test.db')
    original = db.DB_PATH
    db.DB_PATH = path
    db.migrate(path)
    yield path
    db.DB_PATH = original


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    yield connection
    connection.close()
