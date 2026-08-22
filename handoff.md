# Handoff

## Goal

Two goals across this session, in order.

1. **Get Clay's collection into the app** as easily as possible. He is starting
   from zero and the collection is **mostly still boxed**.
2. **Know what exists, not just what he owns** — "find all of the models and box
   sets out there that someone could add to their collection", plus a **weekly
   sweep for new releases**.

He may share the app with other people later; the form is undecided, so nothing
here builds toward accounts and nothing here makes that harder.

## Current state

Working and browser-verified at phone width. **447 tests green.**

Merged: **#17** (onboarding sprint), **#18** (catalogue split, barcode
learning, weekly sweep). Open and green on all four checks: **#19** (catalogue
screen).

The onboarding day runs: scan the shelf without stopping → one tap onboards the
whole queue → pick a box up and scan it to say what's in it → that answer fills
in every copy already recorded. Models with no barcode left to scan get pasted
in at `/add`. `/catalogue` browses what exists.

| Piece | State |
|---|---|
| Rules data (every unit in the game) | 1,445 40k datasheets + 1,450 Kill Team operatives. **Already complete**; re-run the two BSData scripts to refresh |
| Box catalogue (contents, EANs) | 9 entries. Grows weekly + from scanned codes. No bulk source exists |
| Weekly sweep | Armed: Mondays 14:00 UTC (9am Clay's UTC−05:00), fresh session, push notification |
| Scan → sweep → identify → adopt | Built, verified |
| Paste import `/add` | Built, verified |
| Catalogue `/catalogue` | Built, verified (#19) |
| List import (spec §2.7) | **Not built.** Last unbuilt loop step |

## The measurement everything else follows from

A box splits into two halves with very different availability:

- **Contents — findable.** Hivestorm is 11 Tempestus Aquilons + 11 Vespid
  Stingwings, agreed across publisher, sprue-level review, and retailers.
- **Barcodes — essentially not findable.** Every listing carries the GW product
  code (`103-48`); none carries the EAN.

So research supplies contents and cannot supply barcodes — **but the person
holding the box has the number on it.** Hence `adopt_template` now banks the
scanned code against the template, and `barcodes.link_source` records whether a
link came from a scan or the seed. **A scan outranks the seed**; a later scan
can still correct an earlier one.

Egress: only WebSearch works. Every direct fetch (warhammer.com,
warhammer-community, retailers, Lexicanum) is refused by the proxy. Search
enumerates *named box families* well and *individual unit kits* badly.

## Active files

- `seed/data/kits/*.yaml` — the catalogue, one file per faction
- `seed/derived_kits.py` — importer; provenance rules, clears unresolved per run
- `docs/weekly-sweep.md` — the procedure the scheduled session follows
- `scanning.py` — `sweep_queue`, `link_barcode(link_source=)`, `list_templates`
- `collection.py` — `adopt_template` (banks the code), `adopt_all_for_code`
- `lists.py` — `want_template`, `unwant_template`, `wishlist`
- `templates/catalogue.html`, `static/js/catalogue.js`
- `migrations/005_barcode_provenance.sql`, `006_wishlist_from_catalogue.sql`

## Changes made

**#17** — collection screen made actionable; queue sweep; `/box/<code>` and
identify mode; adopt-all by barcode; `/add` paste import; `names.py`.

**#18** — catalogue split per faction with duplicate refusal; barcode learning
from scans; `derived_kits` clears unresolved rows per run; `docs/weekly-sweep.md`;
weekly Routine armed; 4 products researched.

**#19** — `/catalogue` browse/search/filter; want a box (records *which* box, so
the wishlist can name a purchase rather than a parts list); own a box; search
covers units inside a box, not just its name.

## Failed attempts

- **Tried to bulk-import a box catalogue. There isn't one.** GitHub search for a
  GW product/EAN dataset returns **zero** repos. Every candidate host is
  egress-blocked. Do not go looking again without new information.
- **A general "list all kits for faction X" search returns prose, not a list.**
  Enumeration works for named families (Kill Team boxes) and fails for unit kits.
- **Wrote a Playwright check with `.cat-row:has(.cat-want)`** — a *live* locator.
  Once the button's class changed it re-resolved to a different row and reported
  a working feature as broken. The POST had returned 200 the whole time. Use
  `[data-template="N"]`.
- **Ran the drive script against the wrong database.** `DB_PATH` is **not** an
  env var this app reads — `database.py` hardcodes it. Login 401'd, every page
  redirected to `/login`, and the screen looked empty. Set `db.DB_PATH` in a
  launcher instead (`scratchpad/serve.py`).
- **`ps | grep` patterns matching the shell's own command line** → exit 144,
  twice. Use `ps -eo pid,args | awk '/pat/ && !/awk/'`.
- **Numbered a migration 006 when 005 did not exist.** Renumber before applying.
- **Called `lists.want_template` in app.py** — it is imported as `army_lists`.

## Next steps

1. **Merge #19**, then on bastion: `git pull && ./deploy.sh && python3 migrate.py`
   (005 and 006 both pending there) and `python3 seed/derived_kits.py`.
2. **Do a real shelf session.** ~100 boxes against test fixtures is the only way
   to find what is still slow.
3. **Paste the unknown codes** from the review screen for research.
4. **`BACKUP_DEST` is unset** — snapshots sit on the same Jetson as the database.
5. **Two incomplete catalogue entries**, now visible on `/catalogue`: the Daemon
   Prince and GSC Broodcoven premium kits each list only half their contents.
   Needs Clay's decision on the variant/split.
6. **Spec §2.7 list import** — worth retesting now that search is known to work
   and fetching is known not to.
7. **The sharing question** decides whether the collection needs a user column.
   Nothing built so far assumes single-user in the data.
