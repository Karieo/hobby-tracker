-- Photos of finished models, with the date they were finished.
--
-- Clay: "would like to be able to add a picture of the finished model with the
-- date on the collection page."
--
-- A table rather than two columns on `units`, because "the finished model" is
-- one entry in a log and not the only picture worth keeping. A squad gets
-- photographed on sprue, half-painted, and done; a warlord gets photographed
-- from three sides. Columns would have forced a second migration the first
-- time Clay wanted two.
--
-- `taken_on` is a date Clay states, not a timestamp the server observes. The
-- picture of a squad finished on Tuesday is often uploaded on Sunday, and the
-- date that matters is Tuesday's. `created_at` keeps the other one, because
-- "when did this row appear" is a different question and both get asked.
--
-- The file itself lives on disk under data/photos/, not in here. That keeps
-- the database small enough for `sqlite3 .backup` to stay quick; backup.sh and
-- restore.sh carry the directory alongside the snapshot, so the two halves
-- travel together. A photo row whose file is missing renders as a gap rather
-- than a broken page — see photos.for_unit.
CREATE TABLE unit_photos (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id      INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
  -- Server-generated, never anything Clay or his phone chose: a filename that
  -- came from a client is a path-traversal waiting to happen.
  filename     TEXT NOT NULL UNIQUE,
  taken_on     TEXT NOT NULL,
  caption      TEXT,
  content_type TEXT NOT NULL,
  byte_size    INTEGER NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE INDEX idx_unit_photos_unit ON unit_photos(unit_id);
-- Newest first is the default order on the screen, and "what did I finish this
-- month" is the question spec §9 still owes a dashboard for.
CREATE INDEX idx_unit_photos_taken ON unit_photos(taken_on);
