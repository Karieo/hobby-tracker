-- One faction per name, across both games.
--
-- The Kill Team importer prefixed its slugs with `kt-` so its factions could
-- not collide with a 40,000 faction of the same name. The intent was right —
-- a Kill Team of Sisters is a ten-operative team, not the Adepta Sororitas
-- army — but the mechanism was aimed at the wrong column. What actually keeps
-- the two unit lists apart is `datasheets.game_system`; the faction row is
-- just the label Clay picks when tagging an army, a kit or a list, and there
-- he only ever meant one Adepta Sororitas.
--
-- The cost of the prefix was a picker offering the same name twice with no way
-- to tell which was which, on seven screens.
--
-- WHY THIS IS SAFE FOR POINTS
--
-- Points are matched to datasheets by (faction slug, normalised name), and
-- faction scoping there is load-bearing: 35 names carry different points per
-- faction. Sharing a faction row between the games would be a real hazard if
-- Kill Team operatives were in that index — a 40,000 price could land on an
-- operative with the same name.
--
-- They are not. `import_bsdata` builds the index from the datasheets it just
-- wrote in that run (`datasheet_rows`), which is 40,000 only. Kill Team
-- operatives are imported by a separate script that writes no points at all,
-- so no manual entry can resolve onto one.
--
-- Only merges a `kt-` faction into a non-`kt-` one with exactly the same name.
-- A Kill Team team with no 40,000 counterpart — Wrecka Krew, Battleclade — is
-- left alone, because it is not a duplicate of anything.

CREATE TEMPORARY TABLE faction_merge AS
SELECT dupe.id AS from_id, keep.id AS to_id
  FROM factions dupe
  JOIN factions keep
    ON keep.name = dupe.name
   AND keep.slug NOT LIKE 'kt-%'
 WHERE dupe.slug LIKE 'kt-%';

UPDATE datasheets SET faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = datasheets.faction_id)
 WHERE faction_id IN (SELECT from_id FROM faction_merge);

-- Kill Team factions carry no points rows, so this moves nothing today. It is
-- here so the merge stays complete if that ever stops being true.
UPDATE OR IGNORE datasheet_points SET faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = datasheet_points.faction_id)
 WHERE faction_id IN (SELECT from_id FROM faction_merge);

UPDATE armies SET primary_faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = armies.primary_faction_id)
 WHERE primary_faction_id IN (SELECT from_id FROM faction_merge);

UPDATE kit_templates SET faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = kit_templates.faction_id)
 WHERE faction_id IN (SELECT from_id FROM faction_merge);

UPDATE kits SET faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = kits.faction_id)
 WHERE faction_id IN (SELECT from_id FROM faction_merge);

UPDATE army_lists SET faction_id =
  (SELECT to_id FROM faction_merge WHERE from_id = army_lists.faction_id)
 WHERE faction_id IN (SELECT from_id FROM faction_merge);

DELETE FROM factions WHERE id IN (SELECT from_id FROM faction_merge);

DROP TABLE faction_merge;
