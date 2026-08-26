-- Two lists wanting the same unit means buying it once.
--
-- Original spec §7: "deduplicate across lists on the *maximum* requirement,
-- so two lists needing a Deff Dread means buying one." The opposite happened.
-- Each list raised its own whole shortfall, so ten Boyz for Saturday and
-- twenty for Sunday put *thirty* on the wishlist — a shopping list telling
-- Clay to buy ten models he already had covered. Money, on the one screen
-- whose entire job is saying what to buy.
--
-- Deduplicating means one wishlist model can answer several lists at once,
-- and `models.wishlist_source_list_id` cannot say that: it is one column
-- holding one id. Its own comment in `_stamp` already conceded the strain —
-- "wishlist lines are shared now" — while the column stayed single-valued.
-- So the many-to-many fact gets a many-to-many table.
--
-- The two now divide the work, and each keeps one job:
--
--   `wishlist_source_list_id`  this row exists because *a* list asked for it.
--                              It is what separates a list's shortfall from a
--                              standing want Clay added himself, and it is the
--                              pool a later raise tops up rather than adding
--                              alongside.
--   `wishlist_claims`          which lists want it *now*. Many per model, and
--                              what the collection reads to say "· for
--                              Saturday, Sunday".
--
-- Splitting them is what makes the answer independent of the order the lists
-- were raised in. With one column, whichever list ran first owned the models
-- and the other was invisible on the line even while it was waiting on them.
--
-- Both foreign keys cascade. A deleted list drops its claims and leaves the
-- models — Clay still wants them, and they are his to clear, which is exactly
-- what `delete_list` has always done. A deleted model takes its claims with
-- it, so `unwant_template` and `remove_models` need no new cleanup.

CREATE TABLE IF NOT EXISTS wishlist_claims (
    model_id INTEGER NOT NULL REFERENCES models(id)      ON DELETE CASCADE,
    list_id  INTEGER NOT NULL REFERENCES army_lists(id)  ON DELETE CASCADE,
    PRIMARY KEY (model_id, list_id)
);

CREATE INDEX IF NOT EXISTS idx_wishlist_claims_list ON wishlist_claims(list_id);

-- Every model a list has already raised is claimed by that list. Without this
-- the collection would forget which lists raised the wishlist it is already
-- showing, and the first raise after deploying would read an empty pool and
-- stack a second copy on top of what is there.
INSERT OR IGNORE INTO wishlist_claims (model_id, list_id)
SELECT id, wishlist_source_list_id
  FROM models
 WHERE wishlist_source_list_id IS NOT NULL;
