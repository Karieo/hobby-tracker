# Combat Patrol magazine seed data

`combat_patrol_issues.yaml` holds the per-issue contents of the Hachette
Warhammer 40,000: Combat Patrol partwork, issues 1–90. It ships empty on
purpose.

## Why it is empty

Spec §11:

> **the catalogue must be derived, not authored.** A kit list written from a
> model's memory — mine or any other — would be fluent, plausible, and wrong in
> places, with no signal about which places.

That applies here more than anywhere, because this file becomes trusted seed
data for the entire magazine collection. A missing issue costs two minutes at
the review screen. A wrong one corrupts ownership and purchase advice for
months, and there is nothing to prompt anyone to check it.

At the time of writing every published source for the list — the community
contents lists, Hachette's own per-issue pages, and Warhammer Community — was
blocked by this environment's egress policy, so the contents could not be
derived here.

## Filling it in

1. Take the contents from a published source. Prefer one that lists every
   issue in one place, and record the URL under `source.urls`.
2. **Corroborate.** Check a second, independent source and record it under
   `source.corroborated_by`. One source is a guess — the same rule §12 applies
   to scanned box contents.
3. Set `retrieved_on` and `confidence`. Anything you are not sure of should be
   `medium` or `low`, or left out entirely; the importer records the confidence
   on every template it creates.
4. Use the exact datasheet names as BSData has them. The importer matches every
   unit against the imported datasheets and **refuses to invent one** —
   anything it cannot match is reported and written to `unresolved_imports`
   rather than guessed at or dropped.

Then:

```bash
python3 seed/combat_patrol_magazine.py --status     # what is present, what is missing
python3 seed/combat_patrol_magazine.py --dry-run    # match everything, write nothing
python3 seed/combat_patrol_magazine.py --owned-through 75
```

`--owned-through N` also creates the owned kits and their models for issues
1..N, at "On sprue". Templates are created for every issue in the file
regardless, so later issues resolve instantly as they arrive.

Re-running is idempotent: templates are matched on their issue number and
updated rather than duplicated.
