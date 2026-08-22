"""One definition of "the same name", shared by everything that matches one.

Unit names arrive from BSData, from the Munitorum manual, from a seed file
someone typed, and from Clay pasting a shelf into a textarea. Curly apostrophes,
accents, double spaces and case differ between all of them and none of those
differences mean anything — but if two importers fold names even slightly
differently, a unit that matched in one silently fails in the other, and the
failure looks like missing data rather than a mismatch.

So the fold lives here, once. `scripts/import_bsdata.py` re-exports it for the
seeds that already import it from there.
"""

import re
import unicodedata


def norm(s):
    """Fold a unit name to its join key.

    Curly apostrophes, accents and punctuation differ between sources
    ("Grot Tanks" vs "Grot Tanks", "Ork Nob" vs "Ork  Nob"), and none of those
    differences mean anything. Case, punctuation and whitespace all collapse.
    """
    s = unicodedata.normalize('NFKD', s or '')
    s = s.replace('’', "'").replace('‘', "'")
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def slugify(s):
    return norm(s).replace(' ', '-')
