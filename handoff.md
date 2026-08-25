# Handoff

## 1 · Goal

Clay is using the app on his phone, and every session since has been driven by
what he finds when he does. This run is one thread, pulled twice:

> "So all of the kill teams should have a name and a faction. Right now when I
> filter for orks it filters out my ork kill team. Can you take a look at all
> of the faction of the kill teams and make sure they fall into the right
> group."

That became PR #46 (merged). Clay then sent a JSON table of the 2024 bespoke
teams and their factions, which became PR #47 (open).

## 2 · Current state

`main` is green at `c5a2312` — PR #46 merged 02:28 UTC. **PR #47 is open as a
draft** on `claude/new-session-8l17p6`, 696 tests, ShellCheck clean.

**Nothing since #39 is deployed.** bastion is still running the build from
before then; eight PRs have merged behind it.

**Kill Team factions now come from three layers, in increasing authority:**

1. **Derived** — the 2024 game system defines category entries (`Ork`,
   `Aeldari`, `Drukhari`, `Imperium`) and each catalogue references the ones it
   claims. `resolve_factions` takes the narrowest that names a real faction,
   breadth measured by how many catalogues claim it. This is #46, and it tops
   out at 34 teams: `Imperium` covers nineteen while naming no faction, and the
   2021 catalogues carry no categories at all.
2. **Reviewed** — `seed/data/killteam_factions.yaml`, Clay's own table. It
   wins over the derivation, and caught three places where the rule was
   confidently wrong: Hand of the Archon (Aeldari → Drukhari), Brood Brothers
   (Tyranids → Genestealer Cults), Inquisitorial Agents (Astra Militarum →
   Imperial Agents). Every disagreement is printed, never applied silently.
3. **Every printing** — a placement found on one catalogue reaches the other
   printing of the same team, `Kommando` (2021) from `Kommandos` (2024).

679 → 1095 operatives on a real 40,000 faction; 34 → 74 teams placed.

**The reviewed file is trusted for one reason only** — `source.reviewed_by`
says a person reviewed it — so `load_reviewed` refuses a table without
provenance, the same bargain `seed/data/combat_patrol_issues.yaml` makes.

**Three teams are unplaced on purpose.** Fellgor Ravagers, Chaos Cult and
Blooded keep `Chaos`, which names no row. Clay was asked and chose it: filing
them under Chaos Space Marines would be wrong, since Blooded are Traitor Guard
and Fellgor are Beastmen. `test_the_three_chaos_teams_stay_deliberately_unplaced`
pins it so a later tidy-up has to read why first.

**A duplicate faction row is gone.** The name match compared raw strings, so
the compendium team `T'au Empire` missed the faction row `T’au Empire` on the
apostrophe alone and got `kt-t-au-empire` — 24 operatives on a row the army
picker offered twice and no T'au filter reached. `match_faction` is now the one
definition of that lookup, tolerating punctuation and a plural and nothing else.

## 3 · Active files

- `seed/data/killteam_factions.yaml` — **new.** Clay's table, 47 teams, with
  provenance and with his three decisions recorded beside the entries they
  settle.
- `scripts/import_killteam.py` — `load_reviewed`, `reviewed_placements`,
  `real_factions`, `match_faction`, `catalogue_names`, `_singular`;
  `resolve_factions` restructured into the three layers.
- `tests/test_import_killteam.py` — 36 tests, up from 21.
- `CLAUDE.md` — the Rules data section rewritten for the layered resolution.

## 4 · Changes made

PR #46 (merged): faction resolution from catalogue categories, `.gitignore` for
`data/killteam/`. PR #47 (open): the reviewed table, the name-match fix, Clay's
three decisions.

## 5 · Failed attempts

**The category rule was wrong three times and looked right.** Hand of the
Archon derived to Aeldari, Brood Brothers to Tyranids, Inquisitorial Agents to
Astra Militarum. All three are plausible readings of the category links and all
three are wrong. Nothing in the data flags them — only Clay's table did. That
is the whole argument for the reviewed layer winning, and for printing every
disagreement rather than applying it quietly: **an inference that is usually
right is the dangerous kind.**

**The first `_singular` sabotage in the teeth check was too weak to fire.**
Widening `match_faction` to a four-character substring match failed to
mis-file anything in the fixture, so the check passed and said nothing. Only
re-running it with the failure mode the test actually guards — approximating
to the nearest faction rather than reporting — produced the three expected
failures. A teeth check that does not fail has proved nothing; it has to be
sabotage the test is *about*.

**The existing tests would have read the shipped table.** `resolve_factions`
defaulted to `REVIEWED_PATH`, so every derivation test in the file would have
silently mixed both layers. `test_a_team_the_data_cannot_place_is_left_alone`
was already passing for an accidental reason — Battleclade is in Clay's table,
and only the absence of an `Adeptus Mechanicus` row in the fixture DB kept it
unplaced. All 21 now pass `reviewed_path=None` explicitly. **Fourth time this
session a check turned out not to be checking.**

**A migration was nearly written for the duplicate `T'au Empire` row.** It is
importer behaviour, not schema: re-running the importer moves the operatives
onto the real row and leaves the empty `kt-` row behind. Dropping that row is a
separate decision and nothing depends on it being gone.

**`git push --force-with-lease` was rejected as "stale info"** after #46
merged, because GitHub deleted the remote branch and the local tracking ref
still pointed at it. `git remote prune origin` then a plain push. Not a
conflict — there was nothing there to conflict with.

## 6 · Next steps

- **Deploy.** `./backup.sh && git pull && ./deploy.sh` (hyphenated
  `docker-compose` on bastion), then **`python3 scripts/import_killteam.py`
  inside the container** — the Kill Team fixes have no migration and arrive
  with the importer only.
- **`BACKUP_DEST` is unset.** Photos exist only on the Jetson. The one
  outstanding item that loses data if left.
- **The API token pasted into chat is still live** — revoke and re-mint with
  `scripts/api_token.py` inside the container.
- **Greenskin**, the 2021 Ork compendium team, is among the 32 still unplaced.
  Clay's table scopes the Compendium out deliberately; if he owns the box it
  can be added the same reviewed way.
- **Two rules pins have moved** — BSData `13f3c4e5→04c62fcd` (cheap), MFM
  `06754e2f→3c1efe0d` (moves points under existing lists, so a deliberate
  call).
- **Weekly sweep DST shift** — `0 14 * * 1` → `0 15 * * 1` after 1 November.
- `barcodes` and `scan_queue` still have no readers. Dropping them is Clay's
  decision, not a tidy-up.
- Spec §9's remaining dropped requirements; §10 still owes list export as text
  and JSON.
