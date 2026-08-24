-- The pile of things to part with.
--
-- Clay: "Not sold, sell a list of things to part with."
--
-- Migration 010 read that as a disposal — models that had *gone*, excluded
-- from ownership and kept only for history. Wrong tense. This is a shortlist:
-- the models are still on the shelf, still his, still paintable. He has just
-- decided they are going.
--
-- So it is a flag, not a removal. `for_sale_on` says when he decided, and
-- nothing about ownership changes: a model listed for sale still counts, still
-- advances through the stages, still shows in the collection. It simply also
-- appears on a list he can work from.
--
-- The symmetry is the point. Wishlist is want-and-do-not-have; this is
-- have-and-would-rather-not. Both are lists beside the collection rather than
-- states inside it.
--
-- Migration 010's disposal columns stay. Nothing writes them now — the "gone"
-- pile they backed was built on the misreading this corrects — and they are
-- left in place rather than dropped because dropping columns rewrites the
-- table, and an empty column costs nothing while a destructive migration for
-- tidiness costs a restore if it goes wrong.

ALTER TABLE models ADD COLUMN for_sale_on TEXT;

CREATE INDEX IF NOT EXISTS idx_models_for_sale ON models(for_sale_on);
