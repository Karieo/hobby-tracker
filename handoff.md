# Handoff

## 1 · Goal

Clay is using the app on his phone, and every session since has been driven by
what he finds when he does. This run covers three things he asked for and one
he asked to be rid of:

> "Can you go find better flat icons from the web, do not draw them."

> "I would like to pull a list of models, how many I have, then how many
> battle ready."

> "Do a csv that I can download from the site. I also want to filter by
> faction."

> "Drop the kits page, it's not helpful."

## 2 · Current state

`main` is green — 654 tests, ShellCheck clean, CI passing on 3.11 and 3.12.
PRs #40, #41 and #42 are merged. **None of it is deployed**; bastion is
running the build from before #40.

**Stage icons are Lucide**, ISC licensed, vendored inline and pinned to a SHA
in `static/icons/LUCIDE.md` — the same bargain `rules_data.py` makes with
BSData, because an icon set that moves under the app changes a screen with no
commit to point at. Seven of the eight; **On sprue stays hand-drawn** because
Clay preferred it and no icon set has a sprue. Stroke width went 1.6 → 2, which
is what Lucide is drawn for.

**The export narrows two ways.** `?fields=name,owned,battle_ready` and
`?faction=orks` (name or slug, any case). Both refuse rather than shrug: an
unknown field is a 400 listing the valid ones, an unknown faction is a 404
rather than a cheerful empty list.

**`GET /collection.csv`** is the collection screen, downloadable, carrying
exactly the filters the page shows. The link under the tiles says how many rows
it will contain, and the filename says what is in it.

**The Kits screens are gone**, the `kits` table is not. See CLAUDE.md's "The
Kits screens (removed)".

## 3 · Active files

- `templates/_macros.html`, `static/icons/LUCIDE.md` — the icon set and its pin.
- `app.py` — `_export_fields`, `_collection_filters`, `_collection_rows`,
  `/collection.csv`, `faction=` on the export; all `/kits*` routes removed.
- `collection.py` — `EXPORT_FIELDS`. Kit functions untouched and still called.
- `tests/test_suite_hygiene.py` — new; see below.
- `tests/test_routes.py`, `tests/test_collection.py` — the fixture fix and the
  four tests that moved down.

## 4 · Changes made

Six commits across three PRs, `31753b9` through `c5fdb03`.

## 5 · Failed attempts

**Two tests had never run.** Removing the scanner deleted two fixtures and left
their `@pytest.fixture` decorators attached to the tests below them, so pytest
collected `test_a_template_with_no_contents_is_refused` and
`test_home_leads_with_the_effort_weighted_percentage` as *fixtures*. Green
suite, rising count, two unguarded behaviours. Both passed once reconnected —
the assertions were fine, the wiring was not. `tests/test_suite_hygiene.py`
now fails if any `test_*` wears a fixture decorator.

**A test that only passed in company.** `app.py` runs `seed_owner()` at import
time and the `client` fixture imports `app` *inside itself*, so whichever test
imported it first — and only that one — seeded an owner into its own temp
database with the wrong password, and its login failed. Invisible in file
order, reproducible alone. Worse: the fixture **discarded the login response**,
so a failed login degraded ~130 tests into redirect-checkers where every
`assert x not in body` passed vacuously. The fixture now asserts the login
returned 200.

**`-k` silently deselected the test being teeth-checked. Twice.** Both times
the tell was that the *restored* run printed nothing either. A test whose name
contains no word anyone would filter on got renamed. Do not teeth-check with
`-k`; run the file.

**A "restarted" scratch server that had not restarted.** The old process still
held port 3199, so `boot=200` was the stale build answering and a template
change looked like it had not applied. Now the served HTML is asserted to
contain the change before any screenshot is trusted.

**`data-theme` is not the attribute.** It is `data-ground`, values `nuln` and
`blueprint`. Setting the wrong one silently returns the light palette twice and
calls one of them dark.

**The Kits removal nearly took the gap checker with it.** Clay first chose to
drop the data too. `kit_datasheets` is keyed on `kits.id` and is what matches an
unbuilt sprue to a datasheet, so dropping the table would have left
`buildable_from_spare` reading 0 forever with nothing saying why. Put back with
that included, he kept the data. **The destructive migration was written and
then discarded** — check what reads a table before dropping it, not after.

## 6 · Next steps

1. **Deploy to bastion** — `./backup.sh` → `git pull` → `./deploy.sh`, with
   hyphenated `docker-compose`. Picks up all three PRs.
2. **`BACKUP_DEST` is unset.** The photo log is live, so every picture exists
   on the Jetson and nowhere else. The only item here that loses data if left.
3. **An API token was pasted into a chat** and should be revoked and re-minted:
   `docker-compose exec tracker python3 scripts/api_token.py --list|--revoke`.
   Note the *exec* — the host user cannot write the container-owned database,
   which fails as "attempt to write a readonly database".
4. **Two rules pins have moved** — BSData is cheap, the MFM one changes points
   under lists already built. Nothing bumps automatically by design.
5. **The weekly sweep fires at `0 14 * * 1`** — becomes 15:00 local after the
   1 November DST shift.
6. **Spec §9's remaining dropped requirements** — several still owed a
   decision, and §10 still owes list export as text and JSON.
