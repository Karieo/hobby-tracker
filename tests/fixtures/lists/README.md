# List export samples

**One file here is real. Every `synthetic_` file is not.**

`real_orks_2000.txt` is Clay's own 2000-point Ork list, pasted into the
conversation on 2026-08-27 with the words *"Here is the format."* It is the
first real export this repo has ever had, and it immediately did the job the
note below had been asking for: it proved `_newrecruit_count` wrong.

**Which app it came from is not established.** `detect_format` reads it as New
Recruit because it is bulleted, but it does not look like the New Recruit
samples here — its wargear bullets carry no count, and it carries section
headings and a `Total:` trailer they do not. The filename says what is certain
(real, Orks, 2000 points) and claims no format. Rename it when Clay says which
app exported it; do not guess.

What it changed: `list_parse._newrecruit_count` inferred a unit's model count
from bullet *nesting*, and returned 1 for any flat block. Clay's list is flat
from top to bottom — twenty units, ninety-two models, every one read as 1. For
a list that is a survivable under-count the gap report would flag; for `/add`,
which writes the models, it silently under-records a collection by seventy
miniatures.

The fix reads the convention off the document: where any bullet is uncounted,
wargear is the uncounted kind and every counted bullet is a model. Where every
bullet is counted — New Recruit — nothing changed, and
`synthetic_newrecruit_flat.txt` still reads 1 per unit because it is genuinely
ambiguous and must stay that way.

## The synthetic ones

Written from the documented shape of each format, because every candidate host
for a real export is refused by this environment's egress policy — the same wall
that kept spec §2.7 marked "blocked on a source" for weeks. Pasting is the door
that always works, and it is still the only way a real sample gets in here.

So: **replace them.** Export a list from each app, paste it into a file, drop
the `synthetic_` prefix, and the tests will read it. The New Recruit files are
still the ones most worth replacing, because the nesting rule is still what
reads them and it has now been wrong once.

`unknown_*.txt` are deliberately not any format — a chat message, a retyped
sheet, a wargear-only fragment. Those are real shapes even though the text is
invented, and they are what the permissive fallback exists for.
