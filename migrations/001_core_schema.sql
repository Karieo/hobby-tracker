-- 001 · Core schema.
--
-- The whole data model from the spec lands in one migration, not just the
-- tables step 1 and 2 need. Later steps then add rows and views rather than
-- reshaping tables underneath data Clay has already typed in by hand.
--
-- Conventions:
--   * Timestamps are ISO-8601 text in local wall-clock time (Remndrs does the
--     same, and TIMEZONE pins the process zone so "local" is stable).
--   * Money is integer cents. Never floats.
--   * Booleans are INTEGER 0/1.
--   * Enums are TEXT with a CHECK, so a typo fails at write time instead of
--     quietly creating a third box_state nobody queries for.

-- ── Auth (Remndrs posture: single owner, bcrypt, session cookie) ──────────

CREATE TABLE users (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'owner',
  created_at    TEXT NOT NULL
);

CREATE TABLE api_tokens (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL UNIQUE,
  device_name TEXT,
  created_at  TEXT NOT NULL,
  last_used_at TEXT
);

-- ── Reference data ───────────────────────────────────────────────────────

CREATE TABLE factions (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE
);

-- Ordered lookup rather than a hardcoded enum, so the pipeline can be renamed
-- or reordered without a migration. `is_owned` separates "planned" (Wishlist)
-- from "physically on the shelf"; `is_terminal` is the finish line that §7's
-- fieldable/backlog split keys off.
CREATE TABLE stages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  position    INTEGER NOT NULL UNIQUE,
  is_terminal INTEGER NOT NULL DEFAULT 0,
  is_owned    INTEGER NOT NULL DEFAULT 1
);

-- ── Rules data (imported, never hand-entered) ────────────────────────────

CREATE TABLE datasheets (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  bsdata_id  TEXT NOT NULL UNIQUE,   -- stable across renames; the re-sync key
  name       TEXT NOT NULL,
  faction_id INTEGER REFERENCES factions(id),
  min_models INTEGER,
  max_models INTEGER,
  base_size  TEXT,                   -- hand-filled, not in BSData
  effort     INTEGER NOT NULL DEFAULT 1,
  effort_is_override INTEGER NOT NULL DEFAULT 0,  -- protects Clay's edits from re-sync
  -- BSData suffixes alternate printings of a datasheet in brackets:
  -- "[Legends]" for deprecated units, "[Crucible]" for the Crucible of Battle
  -- variant. NULL is a current, standard datasheet. Variants are kept (Clay may
  -- well own the models) but are not expected to carry Munitorum points.
  variant    TEXT,
  source_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_datasheets_faction ON datasheets(faction_id);
CREATE INDEX idx_datasheets_name ON datasheets(name);
CREATE INDEX idx_datasheets_variant ON datasheets(variant);

-- One row per legal unit size. Points are flattened at import time so every
-- lookup is a plain SELECT rather than a modifier evaluation.
--
-- tier_min/tier_max carry 11th edition's Requisition Thresholds: the same unit
-- costs more as your 3rd+ copy ("[1,2]" -> 1/2, "[3,)" -> 3/NULL). The spec
-- predates that mechanic; storing it costs nothing and dropping it would be
-- unrecoverable. v1 reads the tier containing 1 and ignores the rest.
CREATE TABLE datasheet_points (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  datasheet_id   INTEGER NOT NULL REFERENCES datasheets(id) ON DELETE CASCADE,
  -- Which faction is paying. NULL = the datasheet's own faction, which is the
  -- normal case. Set when a Chapter inherits another faction's datasheet at its
  -- own price: there is one Repulsor Executioner datasheet, but it costs a
  -- Blood Angels player 230 and a Black Templar 255. One row each, both true.
  faction_id     INTEGER REFERENCES factions(id),
  model_count    INTEGER NOT NULL,
  points         INTEGER NOT NULL,
  tier_min       INTEGER NOT NULL DEFAULT 1,
  tier_max       INTEGER,             -- NULL = unbounded
  tier_label     TEXT,                -- the manual's verbatim heading
  composition    TEXT,                -- MFM `desc` for composite/named compositions
  effective_from TEXT NOT NULL,
  source_note    TEXT,
  manual_override INTEGER NOT NULL DEFAULT 0,
  UNIQUE (datasheet_id, faction_id, model_count, tier_min, composition)
);
CREATE INDEX idx_points_datasheet ON datasheet_points(datasheet_id);
CREATE INDEX idx_points_faction ON datasheet_points(datasheet_id, faction_id);

-- Anything an importer could not resolve. Never guess, never drop silently —
-- these are the rows the admin page turns into a manual picker.
CREATE TABLE unresolved_imports (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  importer    TEXT NOT NULL,          -- 'bsdata' / 'mfm' / 'kit_catalogue' / 'scan'
  kind        TEXT NOT NULL,          -- 'datasheet' / 'points' / 'faction' / 'unit_line'
  source_ref  TEXT,                   -- file, faction slug, EAN — wherever it came from
  raw_name    TEXT,
  detail      TEXT,                   -- why it could not be resolved
  payload     TEXT,                   -- JSON of the original record, for later replay
  resolved_at TEXT,
  resolved_note TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_unresolved_open ON unresolved_imports(importer, resolved_at);

-- ── Collection ───────────────────────────────────────────────────────────

-- An army is NOT a faction. Clay's Imperial Knights army holds Inquisitor
-- Coteaz and a Callidus Assassin; primary_faction_id is a display label and a
-- list-builder default, never a constraint on what can go in.
CREATE TABLE armies (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  name              TEXT NOT NULL,
  primary_faction_id INTEGER REFERENCES factions(id),
  notes             TEXT,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL
);

-- The product: a box that exists in the world, whether or not Clay owns one.
-- `year` is load-bearing, not cosmetic — Combat Patrol: Orks is both a 2021 and
-- a 2024 box with entirely different contents and Clay owns both.
CREATE TABLE kit_templates (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  name                TEXT NOT NULL,
  faction_id          INTEGER REFERENCES factions(id),
  rrp_cents           INTEGER,
  price_updated_on    TEXT,
  year                INTEGER,
  contents_source     TEXT CHECK (contents_source IN
                        ('manual', 'ean_lookup', 'photo', 'seed')),
  contents_confidence TEXT CHECK (contents_confidence IN ('high', 'medium', 'low')),
  contents_source_urls TEXT,          -- JSON array; traceability for §12 rule 6
  notes               TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE kit_template_units (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  kit_template_id INTEGER NOT NULL REFERENCES kit_templates(id) ON DELETE CASCADE,
  datasheet_id    INTEGER NOT NULL REFERENCES datasheets(id),
  model_count     INTEGER NOT NULL
);
CREATE INDEX idx_ktu_template ON kit_template_units(kit_template_id);

-- A physical box Clay owns.
--
-- box_state is NOT a model stage. A sealed box and one he opened and reshelved
-- both hold models "On sprue", but only one carries a resale premium, and
-- opening is irreversible. Disposals are status changes, never deletions —
-- deleting a sold kit corrupts spend history.
CREATE TABLE kits (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  kit_template_id       INTEGER REFERENCES kit_templates(id),
  name                  TEXT NOT NULL,
  faction_id            INTEGER REFERENCES factions(id),
  source                TEXT CHECK (source IN
                          ('retail', 'magazine_issue', 'premium_kit',
                           'starter_box', 'gift', 'secondhand')),
  source_ref            TEXT,
  acquired_on           TEXT,
  cost_cents            INTEGER,
  box_state             TEXT NOT NULL DEFAULT 'sealed'
                          CHECK (box_state IN ('sealed', 'opened', 'no_box')),
  status                TEXT NOT NULL DEFAULT 'owned'
                          CHECK (status IN ('owned', 'listed', 'sold', 'traded', 'gifted')),
  disposed_on           TEXT,
  disposed_price_cents  INTEGER,
  disposed_note         TEXT,
  photo_url             TEXT,
  notes                 TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);
CREATE INDEX idx_kits_status ON kits(status);
CREATE INDEX idx_kits_template ON kits(kit_template_id);

-- army_id is nullable on purpose: a sealed box Clay has not committed to an
-- army — or is thinking about selling — should not be forced into one.
-- Unassigned units get their own bucket in the collection view, never hidden.
CREATE TABLE units (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  army_id      INTEGER REFERENCES armies(id),
  kit_id       INTEGER REFERENCES kits(id),
  datasheet_id INTEGER NOT NULL REFERENCES datasheets(id),
  nickname     TEXT,
  notes        TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX idx_units_army ON units(army_id);
CREATE INDEX idx_units_kit ON units(kit_id);
CREATE INDEX idx_units_datasheet ON units(datasheet_id);

CREATE TABLE army_lists (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  faction_id   INTEGER REFERENCES factions(id),
  detachment   TEXT,
  points_limit INTEGER,
  notes        TEXT,
  created_at   TEXT NOT NULL
);

-- One row per physical miniature. ~1,500-2,500 rows at Clay's collection size,
-- which SQLite does not care about. Rows are generated in bulk when a kit is
-- instantiated from a template; Clay never creates them one at a time.
CREATE TABLE models (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id                INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
  stage_id               INTEGER NOT NULL REFERENCES stages(id),
  stage_changed_at       TEXT NOT NULL,
  name                   TEXT,
  notes                  TEXT,
  photo_url              TEXT,
  -- Tells an auto-generated "you are short 7 Boyz" want apart from one Clay
  -- deliberately added to the wishlist himself.
  wishlist_source_list_id INTEGER REFERENCES army_lists(id),
  created_at             TEXT NOT NULL
);
CREATE INDEX idx_models_unit ON models(unit_id);
CREATE INDEX idx_models_stage ON models(stage_id);
CREATE INDEX idx_models_stage_changed ON models(stage_changed_at);

-- Append-only. Cheap to write, and the only way "models finished per month"
-- is ever possible — history cannot be reconstructed after the fact.
CREATE TABLE stage_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id      INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  from_stage_id INTEGER REFERENCES stages(id),
  to_stage_id   INTEGER NOT NULL REFERENCES stages(id),
  changed_at    TEXT NOT NULL
);
CREATE INDEX idx_stage_events_model ON stage_events(model_id);
CREATE INDEX idx_stage_events_changed ON stage_events(changed_at);

-- Entries reference datasheets, not specific models. Ownership resolves by
-- counting at query time, so painting something never rewrites a list.
CREATE TABLE list_entries (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id         INTEGER NOT NULL REFERENCES army_lists(id) ON DELETE CASCADE,
  datasheet_id    INTEGER NOT NULL REFERENCES datasheets(id),
  model_count     INTEGER NOT NULL,
  points_snapshot INTEGER,
  is_proxy        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_list_entries_list ON list_entries(list_id);

-- ── Scanning ─────────────────────────────────────────────────────────────

-- The local lookup table Clay builds as he scans — the only part of the
-- barcode chain guaranteed to still work in five years. lookup_* caches
-- whatever an external GTIN provider returned so a code is never looked up
-- twice and a provider disappearing costs nothing already scanned.
CREATE TABLE barcodes (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  code             TEXT NOT NULL UNIQUE,   -- raw EAN-13 / UPC-A digits
  kit_template_id  INTEGER REFERENCES kit_templates(id),
  first_scanned_at TEXT NOT NULL,
  scan_count       INTEGER NOT NULL DEFAULT 1,
  lookup_name      TEXT,
  lookup_mpn       TEXT,
  lookup_provider  TEXT,
  lookup_at        TEXT
);

-- Capture is split from enrichment: each decode lands here immediately so a
-- reload or a dead battery never costs Clay the shelf he just worked through.
-- Resolved rows stay as an audit trail.
CREATE TABLE scan_queue (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code        TEXT NOT NULL,
  quantity    INTEGER NOT NULL DEFAULT 1,
  scanned_at  TEXT NOT NULL,
  photo_url   TEXT,
  resolved_at TEXT,
  kit_id      INTEGER REFERENCES kits(id)
);
CREATE INDEX idx_scan_queue_open ON scan_queue(resolved_at);
