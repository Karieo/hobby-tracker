-- Selling five of twenty Boyz.
--
-- Clay: "I want to add 2 buttons 'sell, trade/giveaway, wishlist more'. I
-- would like to be able to add a number of models to a separate list that
-- includes wishlist."
--
-- Disposal existed only per *kit* — sell a box, the whole box goes. There was
-- no way to say a number, and once the Kits screens were removed there was no
-- way to record a sale at all.
--
-- WHY NOT A "SOLD" STAGE
--
-- The ladder already carries Wishlist at position 0 with is_owned = 0, so a
-- Sold stage at the far end would have made all thirty ownership queries
-- correct for free. It is the wrong shape twice over:
--
--   * It overwrites `stage_id`. "I sold five *painted* Boyz" is the fact worth
--     keeping, and moving them to a stage destroys it.
--   * A stage is hobby progress. This is ownership. The spec already ruled on
--     this shape once — "box_state is not a model stage" — for the same
--     reason: a sealed box and an opened one are both On sprue.
--
-- So disposal sits beside the stage rather than replacing it, and ownership
-- excludes it through one shared SQL fragment, the way `_ACTIVE_UNIT` has
-- always excluded models inside a disposed kit.
--
-- Additive, like every migration here except none. Nothing is rewritten and
-- no row is removed: a disposal is a status change, and the row stays so the
-- spend history can still answer "didn't I used to have one of those?".

ALTER TABLE models ADD COLUMN disposed_on TEXT;

-- Same vocabulary as `kits.status`, minus the two that mean "still mine".
ALTER TABLE models ADD COLUMN disposed_as TEXT
  CHECK (disposed_as IS NULL OR disposed_as IN ('sold', 'traded', 'gifted'));

ALTER TABLE models ADD COLUMN disposed_price_cents INTEGER;

-- Ownership counts filter on this, so it is read on every collection query.
CREATE INDEX IF NOT EXISTS idx_models_disposed ON models(disposed_on);
