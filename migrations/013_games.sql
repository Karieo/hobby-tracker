-- Games played, per list. Clay: "games played by list, win/loss and point
-- difference 0-100".
--
-- He plays in another app — Battlebase — and said so plainly: "Playing the
-- game is a whole other thing." This is not a game tracker and must not grow
-- into one. It records the *outcome* of a list, because a list in this app is
-- a thing you build a collection towards, and whether it actually wins is the
-- one fact about it this app cannot derive from plastic.
--
-- BOTH SCORES, NOT A DIFFERENCE
-- -----------------------------
-- He asked for win/loss and a point difference; asked which way round, he
-- chose to record both totals. That is strictly more than a difference for
-- one extra number typed: "lost 85-90" and "lost 45-90" are two completely
-- different evenings that a stored margin of 5 and 45 would keep apart but a
-- stored *result* would not — and both the result and the margin fall out of
-- the two scores, so neither is stored. Nothing here is a second copy of a
-- fact the row already carries; `SELECT l.*` colliding with `AS points_total`
-- is what that costs.
--
-- Both are NOT NULL, and that is the honest choice rather than the lenient
-- one. A game with no score cannot say who won, so a nullable score would put
-- rows in the tally that no number on the screen could describe — and the
-- tally is the whole feature. If Clay wants to log a game he cannot score,
-- that is a decision to make out loud, not a NULL to leave open.
--
-- The 0-100 range is Clay's, from his own message, not a rule recalled from a
-- rulebook — the same bargain `lists.BATTLE_SIZES` makes with the two battle
-- sizes he took off a screenshot. It is not scoped by game system: Kill Team
-- lists live in `army_lists` too and are not scored this way, but this repo
-- does not write game rules from a model's recall, so the range stays the one
-- number Clay gave and widens only when he says so.
--
-- ON DELETE CASCADE, because a game belongs to its list the way a stage event
-- belongs to its model. Deleting a list is already a deliberate, confirmed act
-- with nothing beside it to undo it; its games go with it.

CREATE TABLE games (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id             INTEGER NOT NULL REFERENCES army_lists(id) ON DELETE CASCADE,
  played_on           TEXT NOT NULL,
  your_score          INTEGER NOT NULL,
  their_score         INTEGER NOT NULL,
  -- Nullable: "lost to Custodes three times" is a pattern worth seeing, and
  -- "played someone, forgot to ask" is a real Tuesday.
  opponent_faction_id INTEGER REFERENCES factions(id),
  notes               TEXT,
  created_at          TEXT NOT NULL
);

CREATE INDEX idx_games_list ON games(list_id);
CREATE INDEX idx_games_played ON games(played_on);
