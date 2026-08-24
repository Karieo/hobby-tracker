# The weekly pin check

A scheduled session runs this once a week. **No human reads the diff**, so it
does exactly one thing and writes nothing.

## What happened to the rest of this

Most of this document was a catalogue sweep: each week, look up the boxes that
went on pre-order since the last run and bank their contents and barcodes in
`seed/data/kits/`, because the app could only recognise a box it had contents
for.

The scanner is gone and the catalogue with it — Clay measured looking a box up
at the till as faster than pointing a camera at it, which was true precisely
because the sweep could never keep ahead of what he was buying. The research
rule that governed it is worth remembering anyway, and lives in CLAUDE.md:
derived from a source, never authored from recall.

What is left is the half that was always separate.

## Check whether the rules data has aged

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

## Shipping

There is usually nothing to ship: this reports, it does not change anything.
Put the result in the report — which source moved, from which commit to which,
or that nothing did.

If a week has nothing to say, **say so.** An empty week is a normal outcome,
and padding a report to look productive is the exact failure this document
exists to prevent.
