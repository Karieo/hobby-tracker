# The weekly catalogue sweep

A scheduled session runs this once a week and merges its own work when CI is
green. **No human reads the diff.** Everything below exists because of that.

## What this is for

The app can only recognise a box it has contents for. There is no open dataset
of Games Workshop box contents — BSData publishes the rules, nobody publishes
the plastic — so the catalogue is built by looking products up one at a time
and banking the answer permanently in `seed/data/kits/`.

This sweep keeps it current: each week, add the products that went on
pre-order or release since the last run.

## The one rule that matters

**Derived, not authored.** Never write contents, a model count, a year or a
barcode from recall. It would be fluent, plausible, and wrong in places, with
no signal about which places — and with nobody reviewing the diff, wrong data
merges itself. If search does not produce it, it does not go in the file.

An entry that cannot be sourced is not a failure of the sweep. Leaving it out
is the sweep working.

## Procedure

1. **Find the week's releases.** Search for the current Sunday Preview,
   pre-orders and release news. Note the product names — those are what the
   rest of the work keys off.

2. **Per product, search for its contents.** What miniatures, and how many of
   each. Two independent sources minimum. Prefer the publisher's own
   announcement, sprue-level reviews, and retailer listings that state counts.

3. **Check the unit names against BSData** before writing them:

   ```
   python3 -c "import database as db; c=db.connect('data/hobby_tracker.db'); \
     print([r[0] for r in c.execute(\"SELECT name FROM datasheets WHERE name LIKE ?\", ('%Boyz%',))])"
   ```

   A brand-new unit will **not** be there — BSData lags a release by days.
   Write the entry anyway with the name as the publisher gives it. The
   importer records it in `unresolved_imports` and a later run resolves it
   once BSData catches up. That is the designed path, not an error.

4. **Write the entry** into `seed/data/kits/<faction>.yaml`, or
   `kill-team.yaml` for Kill Team boxes. Fields:

   ```yaml
   - name: 'Orks: Boyz'
     year: 2026                 # the box's year, not today's
     faction: orks              # slug, matching the factions table
     contents:
       - unit: Boyz             # exactly as BSData names it
         models: 11
     sources:
       urls: [ ... ]            # real URLs actually read
       retrieved_on: '2026-08-22'
       confidence: high|medium|low
       corroborated_by: 3       # how many independent sources agreed
       note: >                  # anything a later reader needs
         ...
   ```

5. **Barcodes: usually leave them off.** Retailers publish the Games Workshop
   product code (`103-48`) constantly and the EAN almost never. A barcode
   needs **two independent sources agreeing** or it does not ship — a wrong
   barcode is silent, attaching wrong contents to a box scanned months later.

   An entry without one still works: it is reachable by name, and the first
   time someone scans that box and adopts the template, the app learns the
   code from the physical box and keeps it forever. That is the intended
   route for most barcodes.

6. **Skip products with no miniatures.** Codexes, dice, card packs and
   datacards are not kits. The importer refuses empty contents, so adding them
   only produces noise.

7. **Watch for the same name, different plastic.** `Combat Patrol: Orks` is a
   2021 box and a 2024 box with completely different contents. `Orks: Boyz` is
   an 11-model 2026 kit and a 10-or-20-model 2018 kit. Always record `year`,
   and if listings disagree about counts, work out whether they are describing
   two different boxes before picking a number.

## Also check whether the rules data has aged

Separate from the catalogue, and quick:

```
python3 scripts/check_rules_pins.py
```

It compares each pinned upstream — the Munitorum Field Manual, BSData's
datasheets, BSData's Kill Team catalogues — against its current HEAD and exits
1 if any has moved. It writes nothing.

**Do not bump a pin.** Report it. Games Workshop reprices with every balance
dataslate, and a list that was legal on Saturday quietly becoming illegal on
Monday is worse than being told and choosing when. The MFM one matters most:
points are what every list screen quotes, and a superseded manual is wrong in
the way that still adds up.

Put it in the report as a line or two — which source moved, from which commit
to which — and leave it there. If the script cannot reach GitHub, say that
instead; "could not check" is a different answer from "nothing moved".

## Verify before shipping

```
python3 seed/derived_kits.py --status      # what is in the files
python3 seed/derived_kits.py --dry-run     # match everything, write nothing
python3 -m pytest -q                       # the provenance rules are tested
```

`--dry-run` must report your new entries with sensible unit counts. Unresolved
lines are acceptable (see step 3); silently skipped entries are not — an entry
reported as `! skipped ... no contents resolved` means every one of its unit
names failed, which usually means they were written wrong.

## Shipping

Commit to the working branch, push, open a PR, and let it auto-merge once CI
is green. If CI fails, fix it — do not merge around it.

If a week has no releases worth adding, **do nothing and say so.** An empty
week is a normal outcome. Never pad the catalogue to make the run look
productive; that is the exact failure this document exists to prevent.
