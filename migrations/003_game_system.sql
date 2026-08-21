-- Kill Team operatives alongside 40,000 datasheets.
--
-- Clay owns Kill Team boxes. Their models are physical miniatures that go from
-- sprue to battle ready exactly like everything else on the shelf, but nothing
-- in `datasheets` could represent them: BSData ships Kill Team as a separate
-- repository (wh40k-killteam, XML .cat files) from wh40k-11e, so the importer
-- had never seen them. A box with no datasheet to point at cannot be recorded
-- at all — which is what "the kill team are missing" turned out to mean.
--
-- One table rather than two. What the collection needs from a datasheet is an
-- identity to hang units and models off, and that is the same shape for a Boyz
-- mob and a Kill Team operative. Splitting them would fork every query that
-- walks units -> datasheets, which is most of them, to solve a problem the
-- collection does not have.
--
-- What it does need is to tell them apart, because they are not
-- interchangeable: points, list building and the gap report are all
-- 40,000-only, and a picker that silently mixes systems invites recording an
-- Intercessor Warrior operative when you meant the Intercessors datasheet.
-- Hence a column, defaulted so every row already imported stays what it was.
ALTER TABLE datasheets ADD COLUMN game_system TEXT NOT NULL DEFAULT 'wh40k';

CREATE INDEX idx_datasheets_system ON datasheets(game_system);
