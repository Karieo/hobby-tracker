# List export samples

**Every file here is invented text. Not one is a verified export from any app.**

That was true before 2026-08-27 and it is still true after, which took a
correction to work out.

## `pasted_orks_2000.txt` — real *input*, invented *format*

Clay pasted this 2000-point Ork list into the conversation with the words
"Here is the format", and it was taken at face value as the repo's first real
export. It is not one. He said afterwards: **"I pasted from Claude trying to
make a list"** — the text was written by a model, and a model writing a
plausible export is exactly the fluent, unsourceable output this repo refuses
everywhere else. It got in through a paste instead of through a seed file.

So it is kept, and it is kept honestly:

- **As an input it is real.** Clay really does paste Claude-written lists into
  `/add` and `/lists/import`, so this is a genuine thing the parser must handle,
  and it stays as a regression fixture for that.
- **As evidence about any app's format it is worth nothing.** It cannot be
  cited for what New Recruit or the GW app writes, and the filename no longer
  says "real".

What it did expose was a genuine bug: `_newrecruit_count` inferred a unit's
model count from bullet *nesting* and returned 1 for any flat block. This list
is flat top to bottom, so twenty units and ninety-two models read as twenty. For
the gap report that is a survivable under-count; for `/add`, which writes the
models, it silently records a collection seventy miniatures short.

The fix keys on a convention **observed in this file** — a model bullet carries
a count, a wargear bullet does not — and fires only when a document contains an
uncounted bullet. It is conservative, and it is unverified: no real export has
ever been read here, so nothing establishes that any app writes that way, or
that a real New Recruit export never contains an uncounted bullet. **A real
sample could disprove it.**

## The `synthetic_` ones

Written from the documented shape of each format, because every candidate host
for a real export is refused by this environment's egress policy — the same wall
that kept spec §2.7 marked "blocked on a source" for weeks. Pasting is the door
that always works, and it is still the only way a real sample gets in here.

So: **replace them**, and this time check the provenance before believing one.
"Export a list from the app and paste the file" is the ask; text that came out
of a chat window is not that, however well-formed it looks. Drop the
`synthetic_` prefix only for something that genuinely came out of an app.

`unknown_*.txt` are deliberately not any format — a chat message, a retyped
sheet, a wargear-only fragment. Those are real shapes even though the text is
invented, and they are what the permissive fallback exists for.
