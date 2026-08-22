# Derived kit catalogue

`derived_kits.yaml` holds boxed-set contents looked up from published sources.
`seed/derived_kits.py` turns them into kit templates, so a box's contents are
established **once per product, ever** — and every copy already on the shelf
resolves behind it.

```bash
python3 seed/derived_kits.py --status     # what's in the file
python3 seed/derived_kits.py --dry-run    # match everything, write nothing
python3 seed/derived_kits.py              # import
```

## Why this file exists at all

There is no open dataset of Games Workshop box contents keyed by EAN. BSData
publishes the rules; nobody publishes the plastic. Every candidate source —
warhammer.com, Hachette, Wahapedia, the GTIN providers — is unreachable from
the environment this was built in, and none of them offers a bulk export
anyway.

So the contents have to be looked up one product at a time, by someone reading
real sources. This file is where that work is banked so it never has to happen
twice.

## The rule that matters

From CLAUDE.md, and it is the one change to this repo that would do real
damage:

> Never write a kit catalogue **or a partwork contents list** from memory — it
> would be fluent, plausible, and wrong in places with no signal about which.

The importer enforces it rather than trusting it. An entry without sources is
refused, and the whole import stops — nothing is written.

## Adding an entry

```yaml
  - name: "Combat Patrol: Orks"
    year: 2024                  # what tells two boxes of the same name apart
    faction: orks               # a factions.slug; scopes the name matching
    barcode: "5011921204021"    # optional — see below
    barcode_sources:            # two, if there is a barcode at all
      - https://…
      - https://…
    contents:
      - unit: Beast Snagga Boyz # must match a BSData datasheet name exactly
        models: 20
    sources:
      urls: [https://…, https://…]
      retrieved_on: 2026-08-22
      confidence: high          # high | medium | low
      corroborated_by: 3        # how many independent sources agreed
      note: >-
        Anything a later reader needs to judge the entry.
```

1. **Corroborate before writing.** `corroborated_by` must be at least 2. One
   source is a guess.
2. **Use exact BSData names.** Every line is matched against the imported
   datasheets, scoped to the faction. Anything that doesn't match is written to
   `unresolved_imports` for a manual pick — never guessed, never dropped. The
   rest of the box still seeds; one bad line is not a lost box.
3. **Set the year** whenever the name has been reused. Templates are matched on
   name *and* year, so 2021 and 2024 Combat Patrol: Orks stay separate boxes.
4. **Be honest about confidence.** It is recorded on every template.

## Barcodes are held to a higher bar

A barcode needs **two independent sources agreeing**, or it doesn't ship. The
importer refuses an unsourced one rather than quietly importing the entry
without it.

The asymmetry is deliberate. Wrong contents under a name are visible the moment
Clay opens the box. A wrong barcode is silent: it attaches the wrong contents to
a box he scans months later, and every count and every piece of purchase advice
built on top of it is wrong with nothing to prompt a check.

This is not hypothetical. Researching Combat Patrol: Necrons turned up two
different EANs across sources, and the sources disagreed about whether they
meant the 2021 box (Overlord, Immortals, Tomb Blades, Night Scythe) or the 2023
one (Overlord, Doomstalker, Skorpekh Destroyers, Warriors, Scarabs). That entry
therefore ships **without** a barcode.

**An entry with no barcode is still worth shipping.** It appears by name in the
review screen's "Contents…" dropdown and on any box page, which is most of the
value. The barcode only buys the automatic suggestion.

## Growing the catalogue

The file starts small on purpose — it holds what could actually be sourced,
not everything that exists. The way it grows is the scanning loop itself:

1. Scan the shelf and sweep the queue. Unknown boxes are recorded as owned.
2. The review screen lists them under *Recorded, contents not yet known*, each
   with its code.
3. Look those codes up — or hand them to an assistant that can search — and add
   the entries here, with their sources.
4. Re-run the importer. Every recorded copy of each box now offers
   **Fill in from *X* →**, and future scans of the same code resolve on sight.

Nothing about this is automatic, and that is the point: contents pre-fill a
review, and Clay confirms. Same bargain the scanner already makes.
