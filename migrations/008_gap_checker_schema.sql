-- Section 7, the list gap checker: what a model *is*, and what it could be.
--
-- WHERE THIS DEVIATES FROM THE SPEC, AND WHY
--
-- Section 7 opens with "Models currently link to kits. Kits don't map cleanly
-- to datasheets ... so the gap checker cannot resolve anything without a direct
-- link." That is not true of this database. `models.unit_id` is NOT NULL and
-- `units.datasheet_id` is NOT NULL, so every model already resolves to exactly
-- one datasheet through its unit, and `collection.inventory()` has been doing
-- precisely that since the collection screen was built. The premise describes
-- an earlier schema.
--
-- The columns are still worth having, for the half of Section 7 that is
-- genuinely new: `is_flexible` records a magnetised model, and `kit_datasheets`
-- records what a box is *capable* of becoming. Neither can be expressed today.
-- So the schema lands as written; only the reasoning changes, and with it the
-- backfill — see below.
--
-- Types: the spec writes `datasheet_id TEXT`. Here `datasheets.id` is an
-- INTEGER PRIMARY KEY, so these are INTEGER. Writing TEXT would still "work" in
-- SQLite and then silently fail every join, which is the worst of both.

-- ── What a model currently is, and whether that is reversible ──────────────
--
-- Three states, per the spec:
--   committed    datasheet_id set, is_flexible 0 — glued as one thing
--   magnetised   datasheet_id set, is_flexible 1 — built as one, swaps in
--                seconds, so it counts as ready for any of its kit's datasheets
--   uncommitted  datasheet_id null — on sprue or assembled but unassigned
--
-- `is_flexible` is never inferred. A model is flexible because Clay said so,
-- and it stays flexible through painting and basing.
ALTER TABLE models ADD COLUMN datasheet_id INTEGER REFERENCES datasheets(id);
ALTER TABLE models ADD COLUMN is_flexible  INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_models_datasheet ON models(datasheet_id);
CREATE INDEX idx_models_flexible  ON models(is_flexible) WHERE is_flexible = 1;

-- BACKFILL.
--
-- The kickoff says "models whose kit has exactly one kit_datasheets row get
-- datasheet_id set. Everything else stays null and gets counted in the output."
-- That rule is for a schema where the kit is the only link. Here the unit is,
-- and it is exact rather than inferred: a model in a Killa Kans unit *is* a
-- Killa Kan, whatever box it came out of and however many datasheets that box
-- covers.
--
-- So every model is backfilled from its unit, and the "uncommitted" population
-- starts empty rather than starting large and needing hand-mapping. That is a
-- better outcome than the kickoff expected, not a shortcut around it: nothing
-- is guessed, because nothing needed guessing.
UPDATE models
   SET datasheet_id = (SELECT u.datasheet_id FROM units u WHERE u.id = models.unit_id)
 WHERE datasheet_id IS NULL;

-- ── What a kit is capable of becoming ──────────────────────────────────────
--
-- An Armiger sprue builds a Helverin or a Warglaive. The big Knight kit builds
-- five-plus datasheets. This is the table that lets allocation pass 2 ask "do I
-- own plastic that could become this?" rather than only "do I own this?".
CREATE TABLE kit_datasheets (
  kit_id       INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
  datasheet_id INTEGER NOT NULL REFERENCES datasheets(id),
  PRIMARY KEY (kit_id, datasheet_id)
);

-- Seeded from two sources, both of them recorded fact rather than inference.
--
-- First the box's catalogue contents: `kit_template_units` is the researched,
-- provenance-carrying list of what a template contains, and every row of it is
-- something a copy of that box can yield.
INSERT OR IGNORE INTO kit_datasheets (kit_id, datasheet_id)
SELECT k.id, ktu.datasheet_id
  FROM kits k
  JOIN kit_template_units ktu ON ktu.kit_template_id = k.kit_template_id;

-- Then what is actually in the box on the shelf. A kit built by hand has no
-- template, and a kit Clay added a unit to after adopting it has a datasheet
-- its template never mentioned. Both are true and neither is in the catalogue.
INSERT OR IGNORE INTO kit_datasheets (kit_id, datasheet_id)
SELECT u.kit_id, u.datasheet_id
  FROM units u
 WHERE u.kit_id IS NOT NULL;

-- ── The learned alias table ────────────────────────────────────────────────
--
-- "This is what makes the feature survive." Every manual resolution writes a
-- row here, so a name is only ever identified once. `alias` is the normalised
-- form, and it is UNIQUE: one spelling means one datasheet, or the picker
-- taught it nothing.
CREATE TABLE datasheet_aliases (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  alias        TEXT NOT NULL UNIQUE,
  datasheet_id INTEGER NOT NULL REFERENCES datasheets(id),
  created_at   TEXT NOT NULL
);

-- ── The list, extended rather than replaced ────────────────────────────────
--
-- The spec asks for a new `lists` table. This database already has
-- `army_lists`, holding lists Clay built by hand and lists he pasted through
-- §2.7, and it is referenced by `models.wishlist_source_list_id` — the link
-- that says which list wanted a wishlisted model. A second, parallel list table
-- would split that in two.
--
-- So the existing one gains the paste columns. `raw_text` is nullable here
-- rather than NOT NULL as the spec writes it, because a hand-built list has no
-- pasted text and is not a defective row.
ALTER TABLE army_lists ADD COLUMN army_id       INTEGER REFERENCES armies(id);
ALTER TABLE army_lists ADD COLUMN raw_text      TEXT;
ALTER TABLE army_lists ADD COLUMN source_format TEXT;
ALTER TABLE army_lists ADD COLUMN points_total  INTEGER;
ALTER TABLE army_lists ADD COLUMN updated_at    TEXT;

UPDATE army_lists SET updated_at = created_at WHERE updated_at IS NULL;

-- ── list_entries: rebuilt, because a column has to lose NOT NULL ───────────
--
-- An unresolved entry is the whole point of the parser contract: a line that
-- matched nothing becomes a visible row Clay fixes by hand, never a dropped
-- line. That needs `datasheet_id` nullable, and SQLite cannot drop NOT NULL in
-- place — the table has to be rebuilt.
--
-- Safe to rebuild with foreign keys ON: nothing references `list_entries`.
-- Verified against every migration in this directory before writing this.
--
-- Two points columns, and they are not redundant:
--   points_snapshot  what this app priced the entry at from the Munitorum
--                    manual, scoped by faction. Authoritative.
--   points           what the export declared. Recorded because re-parsing
--                    stored text should keep everything the text said, and
--                    because a mismatch is worth being able to see.
-- Which one the report totals is a decision for commit 5, and the standing
-- rule is that a number out of someone else's app never outranks the official
-- one.
CREATE TABLE list_entries_new (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id         INTEGER NOT NULL REFERENCES army_lists(id) ON DELETE CASCADE,
  position        INTEGER NOT NULL DEFAULT 0,
  raw_name        TEXT,
  datasheet_id    INTEGER REFERENCES datasheets(id),
  model_count     INTEGER NOT NULL,
  points_snapshot INTEGER,
  points          INTEGER,
  resolved_by     TEXT CHECK (resolved_by IS NULL OR resolved_by IN
                    ('exact', 'fuzzy', 'alias', 'manual')),
  is_proxy        INTEGER NOT NULL DEFAULT 0
);

-- Existing rows were all resolved when they were added — an entry could not be
-- created without a datasheet — so they carry resolved_by 'manual': Clay picked
-- them, one way or another. Position follows the order they were entered in,
-- per list, which is the order they have always displayed in.
INSERT INTO list_entries_new
       (id, list_id, position, raw_name, datasheet_id, model_count,
        points_snapshot, points, resolved_by, is_proxy)
SELECT e.id, e.list_id,
       (SELECT COUNT(*) FROM list_entries earlier
         WHERE earlier.list_id = e.list_id AND earlier.id < e.id),
       NULL, e.datasheet_id, e.model_count, e.points_snapshot, NULL,
       'manual', e.is_proxy
  FROM list_entries e;

DROP TABLE list_entries;
ALTER TABLE list_entries_new RENAME TO list_entries;

CREATE INDEX idx_list_entries_list       ON list_entries(list_id);
CREATE INDEX idx_list_entries_unresolved ON list_entries(list_id)
  WHERE datasheet_id IS NULL;
CREATE INDEX idx_datasheet_aliases_sheet ON datasheet_aliases(datasheet_id);
