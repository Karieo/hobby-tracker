-- Which box a wanted model came from.
--
-- `wishlist_source_list_id` already records that five Nobz are wanted *for
-- Saturday's list*, which is what tells a shortfall apart from a standing
-- want. This is the same idea from the other direction: wanting a box from the
-- catalogue records the box.
--
-- It is what lets the shopping list name a thing Clay can actually buy. "11
-- Boyz, 1 Trukk" is a parts list; "Orks: Trukk Boyz" is a purchase. Without
-- this the catalogue could put models on the wishlist but not say where they
-- come from, and he would be back to working out which box to pick up.
--
-- It also makes wanting a box idempotent per box, the same bargain
-- `raise_wishlist` makes per list: tapping "Want it" twice tops up to the
-- box's contents rather than stacking a second copy.
ALTER TABLE models ADD COLUMN wishlist_source_template_id INTEGER
  REFERENCES kit_templates(id);
