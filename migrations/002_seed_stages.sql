-- 002 · Seed the stage pipeline.
--
-- Positions follow the spec's numbering exactly, including Wishlist at 0.
-- Wishlist is a stage, not a table: a wanted model is a real `models` row in a
-- real unit, so it shows up in that army's bars as unfinished, contributes to
-- total effort, and converts to owned through the same stage-change action as
-- everything else. `is_owned = 0` is what keeps it out of ownership counts.
--
-- "Battle ready" is Clay's finish line, not GW's minimum standard: painted,
-- based, and he considers it done. Only this stage counts as fieldable;
-- everything earlier is backlog.
--
-- Stages are seeded, not hardcoded — renaming or reordering the pipeline later
-- is an UPDATE, not a migration.

INSERT INTO stages (name, position, is_terminal, is_owned) VALUES
  ('Wishlist',       0, 0, 0),
  ('On sprue',       1, 0, 1),
  ('Assembled',      2, 0, 1),
  ('Base prepared',  3, 0, 1),
  ('Primed',         4, 0, 1),
  ('Painted',        5, 0, 1),
  ('Based',          6, 0, 1),
  ('Battle ready',   7, 1, 1);
