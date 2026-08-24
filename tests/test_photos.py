"""The hobby log: pictures of finished models, and the dates they were finished.

Clay: "would like to be able to add a picture of the finished model with the
date on the collection page."

The thing worth testing hardest is not the upload — it is that the two halves
stay together. A row in `unit_photos` points at a file under `data/photos/`,
so the database and the directory have to be backed up as one thing or the
restore produces a log of missing pictures.
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import photos

# Smallest things that sniff correctly. Not valid images beyond their headers,
# which is the point: what is checked is the signature, not decodability.
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 40
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
WEBP = b'RIFF' + struct.pack('<I', 40) + b'WEBP' + b'\x00' * 32
HEIC = b'\x00\x00\x00\x18ftypheic' + b'\x00' * 32


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, 'PHOTO_DIR', str(tmp_path / 'photos'))
    path = str(tmp_path / 'shots.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def unit(conn):
    faction = db.upsert_faction(conn, 'Orks', 'orks')
    datasheet = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?,?,?,1,?,?)',
        ('boyz', 'Boyz', faction, db.now(), db.now())).lastrowid
    return col.create_unit(conn, datasheet, 10)


# ── What counts as a picture ─────────────────────────────

@pytest.mark.parametrize('data,expected', [
    (JPEG, 'image/jpeg'), (PNG, 'image/png'),
    (WEBP, 'image/webp'), (HEIC, 'image/heic'),
])
def test_the_formats_a_phone_produces(conn, unit, data, expected):
    """HEIC included. Safari converts it for most file inputs and not all, and
    a photo Clay cannot upload is worse than one a desktop browser will not
    decode."""
    saved = photos.add(conn, unit, data)
    assert saved['content_type'] == expected


def test_something_that_is_not_an_image_is_refused(conn, unit):
    with pytest.raises(photos.PhotoError):
        photos.add(conn, unit, b'PK\x03\x04 this is a zip')


def test_the_signature_beats_the_extension(conn, unit):
    """A share sheet that labels a PNG .jpg is a normal Tuesday. What is stored
    is what the bytes say."""
    saved = photos.add(conn, unit, PNG)
    assert saved['content_type'] == 'image/png'
    assert saved['filename'].endswith('.png')


def test_an_empty_upload_says_so(conn, unit):
    with pytest.raises(photos.PhotoError):
        photos.add(conn, unit, b'')


def test_something_enormous_is_refused(conn, unit, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_BYTES', 1024)
    with pytest.raises(photos.PhotoError):
        photos.add(conn, unit, JPEG + b'\x00' * 2048)


# ── The date is Clay's, not the clock's ──────────────────

def test_the_date_is_the_one_clay_gives(conn, unit):
    """The squad finished on Tuesday is photographed then and uploaded on
    Sunday. Tuesday is the date that means anything."""
    photos.add(conn, unit, JPEG, taken_on='2026-08-18')
    assert photos.for_unit(conn, unit)[0]['taken_on'] == '2026-08-18'


def test_without_a_date_it_is_today(conn, unit):
    from datetime import date
    photos.add(conn, unit, JPEG)
    assert photos.for_unit(conn, unit)[0]['taken_on'] == date.today().isoformat()


def test_the_log_reads_newest_first(conn, unit):
    for day in ('2026-08-01', '2026-08-20', '2026-08-10'):
        photos.add(conn, unit, JPEG, taken_on=day)
    assert [s['taken_on'] for s in photos.for_unit(conn, unit)] == \
        ['2026-08-20', '2026-08-10', '2026-08-01']


# ── The row and the file stay together ───────────────────

def test_the_file_lands_where_the_row_says(conn, unit):
    saved = photos.add(conn, unit, JPEG, caption='first ten done')
    on_disk = os.path.join(photos.PHOTO_DIR, saved['filename'])
    assert os.path.exists(on_disk)
    with open(on_disk, 'rb') as fh:
        assert fh.read() == JPEG


def test_a_filename_is_never_the_uploads(conn, unit):
    """It is generated here. A name that came from a client is a path traversal
    waiting for someone to try `../`."""
    saved = photos.add(conn, unit, JPEG)
    assert '/' not in saved['filename'] and '..' not in saved['filename']
    assert saved['filename'].endswith('.jpg')
    assert len(saved['filename']) == 36, '32 hex characters plus the suffix'


def test_a_failed_insert_leaves_no_orphan_file(conn, unit):
    """Nothing would ever list it, so nothing would ever delete it either."""
    before = os.listdir(photos.PHOTO_DIR) if os.path.isdir(photos.PHOTO_DIR) else []
    with pytest.raises(Exception):
        photos.add(conn, 99999, JPEG)          # no such unit: FK refuses
    after = os.listdir(photos.PHOTO_DIR) if os.path.isdir(photos.PHOTO_DIR) else []
    assert after == before


def test_deleting_takes_the_file_too(conn, unit):
    saved = photos.add(conn, unit, JPEG)
    path = os.path.join(photos.PHOTO_DIR, saved['filename'])

    assert photos.delete(conn, saved['id']) == unit

    assert photos.for_unit(conn, unit) == []
    assert not os.path.exists(path)


def test_deleting_a_photo_whose_file_already_went(conn, unit):
    """Restores and stray `rm`s happen. The row must still be removable."""
    saved = photos.add(conn, unit, JPEG)
    os.unlink(os.path.join(photos.PHOTO_DIR, saved['filename']))

    assert photos.delete(conn, saved['id']) == unit
    assert photos.for_unit(conn, unit) == []


def test_deleting_the_unit_takes_its_photos(conn, unit):
    """ON DELETE CASCADE. Rows for a unit that is gone would be unreachable
    and un-deletable."""
    photos.add(conn, unit, JPEG)
    col.delete_unit(conn, unit)
    assert conn.execute(
        'SELECT COUNT(*) AS n FROM unit_photos').fetchone()['n'] == 0


def test_a_missing_file_is_reported_not_rendered(conn, unit):
    """A restore that brought the database without data/photos/. The screen
    says so rather than showing a broken image, because "the backup carried
    half of it" is worth finding out from a caption."""
    saved = photos.add(conn, unit, JPEG)
    os.unlink(os.path.join(photos.PHOTO_DIR, saved['filename']))

    assert photos.for_unit(conn, unit)[0]['missing'] is True


def test_a_present_file_is_not_flagged(conn, unit):
    photos.add(conn, unit, JPEG)
    assert photos.for_unit(conn, unit)[0]['missing'] is False


# ── Serving ──────────────────────────────────────────────

def test_path_for_refuses_anything_with_a_separator(conn, unit):
    photos.add(conn, unit, JPEG)
    for attempt in ('../hobby_tracker.db', 'a/b.jpg', '/etc/passwd', ''):
        assert photos.path_for(attempt) is None


def test_path_for_finds_a_real_one(conn, unit):
    saved = photos.add(conn, unit, JPEG)
    assert photos.path_for(saved['filename'])


def test_path_for_a_name_that_is_not_there(conn, unit):
    assert photos.path_for('deadbeef.jpg') is None
