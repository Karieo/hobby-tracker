"""Pictures of finished models, and the dates they were finished.

Spec §2.4's "photo per model" arrives here as a photo per *unit*, in a dated
log: a squad gets photographed on sprue, half-painted and done, and a warlord
gets photographed from three sides. One column would have forced a second
migration the first time Clay wanted two pictures of the same thing.

WHY THE FILE IS NOT IN THE DATABASE
-----------------------------------
`backup.sh` snapshots the database with `sqlite3 .backup`, which is fast
because the database is small. A few hundred phone photos would put a gigabyte
of BLOBs through that on a Jetson every night.

So the bytes live under `data/photos/` and the row points at them — which
means **the two halves have to be backed up together or not at all**. That is
the trade, and it is only safe because `backup.sh` and `restore.sh` carry the
directory alongside the snapshot. Storing files on disk without teaching the
backup about them is how a hobby log quietly becomes a list of missing images.

WHAT COMES OFF AN IPHONE
------------------------
Safari converts HEIC to JPEG for most file inputs, but not all iOS versions and
not every share path, so HEIC is accepted rather than refused: a photo Clay
cannot upload is worse than one that renders as a download link on a desktop
browser that will not decode it.

The filename is generated here and never taken from the upload. A name that
came from a client is a path traversal waiting for someone to try `../`.
"""

import os
import secrets
from datetime import date

import database as db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE_DIR, 'data', 'photos')

#: Magic bytes, checked instead of trusting the browser's Content-Type. A
#: mislabelled upload is usually a share-sheet quirk rather than an attack, but
#: either way what matters is what the file *is*.
_SIGNATURES = (
    (b'\xff\xd8\xff', 'image/jpeg', '.jpg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png', '.png'),
    (b'GIF87a', 'image/gif', '.gif'),
    (b'GIF89a', 'image/gif', '.gif'),
)

#: The two container formats whose magic sits at offset 4 rather than 0.
_FTYP_BRANDS = {
    b'heic': ('image/heic', '.heic'), b'heix': ('image/heic', '.heic'),
    b'hevc': ('image/heic', '.heic'), b'heim': ('image/heic', '.heic'),
    b'mif1': ('image/heif', '.heif'), b'msf1': ('image/heif', '.heif'),
    b'avif': ('image/avif', '.avif'),
}

MAX_BYTES = 20 * 1024 * 1024


class PhotoError(ValueError):
    """Something about the upload is wrong in a way Clay can act on."""


def sniff(head):
    """What this actually is, from its first bytes. None if unrecognised."""
    for magic, content_type, suffix in _SIGNATURES:
        if head.startswith(magic):
            return content_type, suffix
    # RIFF....WEBP — the size sits between the two markers.
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    # ISO base media: a four-byte size, 'ftyp', then the brand.
    if head[4:8] == b'ftyp':
        brand = _FTYP_BRANDS.get(head[8:12])
        if brand:
            return brand
    return None


def add(conn, unit_id, data, taken_on=None, caption=None):
    """Save an uploaded image and record it against a unit.

    ``taken_on`` is the date Clay says the models were finished, defaulting to
    today. The picture of a squad finished on Tuesday is often uploaded on
    Sunday, and Tuesday is the date that means anything.
    """
    if not data:
        raise PhotoError('That upload was empty')
    if len(data) > MAX_BYTES:
        raise PhotoError(
            f'{len(data) // (1024 * 1024)} MB is bigger than the '
            f'{MAX_BYTES // (1024 * 1024)} MB limit')
    kind = sniff(data[:16])
    if not kind:
        raise PhotoError('That does not look like an image')
    content_type, suffix = kind

    os.makedirs(PHOTO_DIR, exist_ok=True)
    filename = f'{secrets.token_hex(16)}{suffix}'
    path = os.path.join(PHOTO_DIR, filename)
    with open(path, 'wb') as fh:
        fh.write(data)

    try:
        cur = conn.execute(
            'INSERT INTO unit_photos (unit_id, filename, taken_on, caption, '
            'content_type, byte_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (unit_id, filename, taken_on or date.today().isoformat(),
             (caption or '').strip() or None, content_type, len(data), db.now()))
    except Exception:
        # Never leave a file with no row pointing at it: nothing would ever
        # list it, so nothing would ever delete it either.
        os.unlink(path)
        raise
    return {'id': cur.lastrowid, 'filename': filename,
            'content_type': content_type, 'byte_size': len(data)}


def for_unit(conn, unit_id):
    """Every photo of a unit, newest date first.

    ``missing`` is set when the row is here and the file is not — a restore
    that brought the database and not the directory, most likely. The screen
    says so rather than rendering a broken image, because "the backup only
    carried half of it" is worth finding out from a caption rather than from a
    grey box.
    """
    rows = [dict(r) for r in conn.execute("""
        SELECT id, unit_id, filename, taken_on, caption, content_type,
               byte_size, created_at
          FROM unit_photos
         WHERE unit_id = ?
         ORDER BY taken_on DESC, id DESC
    """, (unit_id,))]
    for row in rows:
        row['missing'] = not os.path.exists(
            os.path.join(PHOTO_DIR, row['filename']))
    return rows


_EDITABLE = ('taken_on', 'caption')


def update(conn, photo_id, **fields):
    """Change what a picture says about itself, after the fact.

    The caption is the part most likely to arrive late: the picture gets taken
    and uploaded in the same breath, and what it was worth saying about turns
    up a day later. Making it a field on the upload form and nowhere else meant
    a photo's note had exactly one moment to exist in.

    Only supplied keys are written, for the same reason `collection.update_unit`
    does it: a form that sends one field must not blank the other. An empty
    caption still clears — a caption you want gone is a real thing to want.
    `taken_on` is different: blank would leave a photo with no date at all in a
    log whose whole axis is dates, so it is ignored rather than honoured.
    """
    unknown = set(fields) - set(_EDITABLE)
    assert not unknown, f'update cannot write {sorted(unknown)}'
    fields = {k: v for k, v in fields.items()
              if not (k == 'taken_on' and not (v or '').strip())}
    if not fields:
        return False
    values = [(v or '').strip() or None if k == 'caption' else v
              for k, v in fields.items()]
    sets = ', '.join(f'{name} = ?' for name in fields)
    cur = conn.execute(f'UPDATE unit_photos SET {sets} WHERE id = ?',
                       [*values, photo_id])
    return cur.rowcount > 0


def timeline(conn, limit=500):
    """Every picture, oldest first — the journey rather than the log.

    Oldest first is the whole point and the opposite of `for_unit`. A unit page
    answers "what does this look like now", so the newest picture belongs at
    the top; a timeline answers "how did this get here", and that reads
    forwards.

    Missing files are flagged the same way and kept in the sequence rather than
    dropped, because a gap in a journey is information too.
    """
    rows = [dict(r) for r in conn.execute("""
        SELECT p.id, p.unit_id, p.filename, p.taken_on, p.caption,
               p.created_at,
               COALESCE(u.nickname, d.name) AS unit_name,
               d.name  AS datasheet_name,
               f.name  AS faction_name,
               a.name  AS army_name
          FROM unit_photos p
          JOIN units u          ON u.id = p.unit_id
          JOIN datasheets d     ON d.id = u.datasheet_id
          LEFT JOIN factions f  ON f.id = d.faction_id
          LEFT JOIN armies a    ON a.id = u.army_id
         ORDER BY p.taken_on, p.id
         LIMIT ?
    """, (limit,))]
    for row in rows:
        row['missing'] = not os.path.exists(
            os.path.join(PHOTO_DIR, row['filename']))
    return rows


def get(conn, photo_id):
    row = conn.execute('SELECT * FROM unit_photos WHERE id = ?',
                       (photo_id,)).fetchone()
    return dict(row) if row else None


def delete(conn, photo_id):
    """Drop the row and the file. Returns the unit it belonged to, or None.

    Row first: a file with no row is invisible and immortal, while a row with
    no file is a gap the screen already knows how to describe. If the unlink
    fails the row is still gone, which is the right way round.
    """
    row = get(conn, photo_id)
    if not row:
        return None
    conn.execute('DELETE FROM unit_photos WHERE id = ?', (photo_id,))
    path = os.path.join(PHOTO_DIR, row['filename'])
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    return row['unit_id']


def path_for(filename):
    """Absolute path of a stored photo, or None if the name is not one of ours.

    The name always comes from the database rather than from a URL, but this
    refuses anything with a separator in it anyway. The one time that check is
    missing is the time a route grows a parameter it did not have when it was
    written.
    """
    if not filename or os.path.basename(filename) != filename:
        return None
    path = os.path.join(PHOTO_DIR, filename)
    return path if os.path.exists(path) else None
