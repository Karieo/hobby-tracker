"""Reading an army list export, without ever losing a line.

Section 7's parser. Three handlers tried in order — New Recruit, the GW app,
and a permissive fallback — each turning pasted text into `ParsedEntry` rows
with a detected `source_format`.

The one rule that outranks everything else here: **a line is never silently
dropped**. Wargear and bullet detail are discarded on purpose, and section
headings are scaffolding rather than units, but anything else that cannot be
read becomes a visible entry carrying the whole line as its `raw_name`. A
missing unit makes the whole report wrong in a way Clay will not notice until
he is standing at a table without it.

WHY THIS IS NOT `bulk_add.parse_lines`
--------------------------------------
The collection paste answers "what do I own", reads stage words ("20 Boyz
built"), and is free to skip a line it cannot use — a shelf typed from memory
is lossy by nature. This answers "can I field this", carries points and
position, and may not skip anything. Same shape of input, different contract.
The scaffolding patterns are shared rather than copied, because two regexes
drifting apart is how "it works in one place" starts.

WHAT IS MOST LIKELY WRONG HERE
------------------------------
`tests/fixtures/lists/` holds **synthetic** samples. Every candidate host for a
real export is refused by egress policy, so the formats are implemented from
their documented shape rather than from something measured, and the model-count
rule for New Recruit (see `_newrecruit_count`) is the part most likely to need
correcting against a real paste. It is deliberately biased toward under-
counting rather than over-counting: a list that says "you need 1 Warboss" when
it meant 20 Boyz is visibly odd, while one that says "you need 3 Warbosses"
sends Clay to a shop to buy two he does not need, which is the exact failure
this whole feature exists to prevent.
"""

import re
from typing import NamedTuple, Optional

from bulk_add import POINTS_RE, SECTION_RE, TOTAL_RE

NEWRECRUIT = 'newrecruit'
GW_APP = 'gw_app'
UNKNOWN = 'unknown'


class ParsedEntry(NamedTuple):
    """One unit as the export wrote it, before anything is resolved.

    `model_count` defaults to 1 rather than to nothing: a character entry
    usually carries no count and means one model, which is spec case 4. An
    entry the parser could not read keeps the whole line as `raw_name` and
    takes that same default — harmless, because an unresolved entry is
    excluded from every total until Clay picks a datasheet for it.
    """
    raw_name: str
    model_count: int = 1
    points: Optional[int] = None
    position: int = 0


class ParsedList(NamedTuple):
    """`discarded` counts the lines deliberately thrown away — wargear detail,
    section headings, and the preamble naming the list and its faction. Kept as
    a number so a paste that yields nothing can say *why* rather than showing an
    empty table: twelve wargear lines and no units is a different mistake from
    an empty textarea."""
    source_format: str
    entries: list
    points_total: Optional[int] = None
    discarded: int = 0


# A unit line with its points in brackets: "Boyz (180 points)", "Warboss (65)".
_NAMED_POINTS = re.compile(
    r'^(?P<name>.+?)\s*[\(\[]\s*(?P<points>\d+)\s*(?:points?|pts?)?\s*[\)\]]\s*$',
    re.IGNORECASE)

# The GW app's shape, count first and points in square brackets:
#   "10x Boyz [90pts]"
_GW_ENTRY = re.compile(
    r'^(?P<count>\d+)\s*x\s+(?P<name>.+?)\s*'
    r'\[\s*(?P<points>\d+)\s*(?:pts?|points?)?\s*\]\s*$',
    re.IGNORECASE)

# Any bullet, at any depth. The indent and the marker together are what say
# whether a line is a model in the unit or a weapon on one of them.
_BULLET = re.compile(r'^(?P<indent>\s*)(?P<marker>[•·▪◦‣*\-–])\s*(?P<body>.*)$')

# "19x Ork Boy" inside a bullet.
_COUNTED = re.compile(r'^(?P<count>\d+)\s*x\s+(?P<name>.+)$', re.IGNORECASE)

# The declared total, which is the export's own arithmetic rather than ours.
_DECLARED_TOTAL = re.compile(
    r'\(\s*(?P<points>\d+)\s*(?:points?|pts?)\s*\)', re.IGNORECASE)

# Section headings people and apps both write. Units never look like these.
_HEADING = re.compile(
    r'^\s*(?:characters?|battleline|infantry|vehicles?|monsters?|'
    r'dedicated\s+transports?|other\s+datasheets?|allied\s+units?|'
    r'fortifications?|epic\s+heroes?|beasts?|swarms?)\s*$', re.IGNORECASE)


def parse(text):
    """Read pasted text into a `ParsedList`. Never raises on bad input."""
    lines = (text or '').splitlines()
    fmt = detect_format(lines)
    body = _body(lines, fmt)
    handler = {NEWRECRUIT: _parse_newrecruit, GW_APP: _parse_gw_app}.get(
        fmt, _parse_permissive)
    entries = handler(body)
    kept = len([line for line in lines if line.strip()])
    return ParsedList(fmt, entries, declared_total(lines),
                      max(0, kept - len(entries)))


def _body(lines, fmt):
    """Drop the preamble — everything before the units start.

    Both real formats open with the list's name, its faction, its battle size
    and its detachment. None of those are units, and reporting four of them as
    unknown datasheets on every single paste would teach Clay to ignore the
    unresolved rows, which are the one thing in this feature he must not learn
    to ignore.

    A section heading is the reliable marker, so use it when there is one. When
    there is not — a short export, or a paste that lost its headings on the way
    through a chat window — fall back to skipping up to the first line that
    actually looks like a counted entry. A character with no count still gets
    kept, because it comes after that point.

    A heading counts wherever it appears, including in a paste no handler
    recognised: `CHARACTERS` on a line of its own is structure, and a list whose
    units were all deleted before pasting should read as no units rather than
    as two phantom ones called "Da Green Tide" and "Orks".

    Without a heading the permissive handler skips nothing. A paste with no
    recognisable shape has no reliable preamble either, and guessing at one
    there costs a unit.
    """
    for i, line in enumerate(lines):
        if _HEADING.match(line.strip()):
            return lines[i + 1:]
    if fmt == UNKNOWN:
        return lines
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _GW_ENTRY.match(stripped) or _BULLET.match(line):
            return lines[i:] if _GW_ENTRY.match(stripped) else lines[max(0, i - 1):]
    return lines


def detect_format(lines):
    """Which handler reads this best, decided by what the body looks like.

    Not by a header string. Exports get copied out of a chat message with the
    first two lines lost, and a format detected from a banner that is no longer
    there falls back to the permissive handler for a list it could have read
    properly. Counting the shapes that only one format produces survives that.
    """
    gw = nr = 0
    for line in lines:
        if _GW_ENTRY.match(line.strip()):
            gw += 1
            continue
        bullet = _BULLET.match(line)
        if bullet and _COUNTED.match(bullet.group('body').strip()):
            nr += 1
    if gw and gw >= nr:
        return GW_APP
    if nr:
        return NEWRECRUIT
    return UNKNOWN


def declared_total(lines):
    """The points total the export claims, for showing beside our own.

    Read from the first bracketed points figure, which in both formats is the
    list's own header. Never used as the app's number — §2.7 settled that this
    app prices a list from the Munitorum manual, and an export's arithmetic is
    at best a duplicate of ours and at worst a previous edition's.
    """
    for line in lines:
        if not line.strip() or _is_scaffolding(line.strip()):
            continue
        found = _DECLARED_TOTAL.search(line)
        if found:
            return int(found.group('points'))
        break
    return None


def _is_scaffolding(line):
    """Section markers, headings and totals — present in every export, never
    a unit, and reporting them as unresolved names would bury the lines that
    genuinely need a decision."""
    return bool(SECTION_RE.match(line) or TOTAL_RE.match(line)
                or _HEADING.match(line))


def _blocks(lines):
    """Group the paste into (header_line, bullet_lines) per unit.

    A non-bullet line opens a block; the indented bullets under it belong to
    it. Blank lines and scaffolding close nothing and belong to nobody.
    """
    out = []
    current = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if _BULLET.match(line):
            if current is not None:
                current[1].append(line)
            # A bullet before any header is orphan detail — wargear from a
            # truncated paste. Discarded, because it names no unit.
            continue
        if _is_scaffolding(line.strip()):
            current = None
            continue
        current = (line.strip(), [])
        out.append(current)
    return out


def _uncounted_wargear(lines):
    """Does this document ever write a bullet without a count?

    That is the tell for the convention where wargear is uncounted and every
    counted bullet is therefore a model. Read once over the whole paste, since
    a single block cannot say which convention it is written in.
    """
    for line in lines:
        found = _BULLET.match(line)
        if found and found.group('body').strip() and not _COUNTED.match(
                found.group('body').strip()):
            return True
    return False

def _newrecruit_count(bullets, uncounted_wargear=False):
    """How many models the bullet block describes.

    This is the ambiguous part of the format and the reason the fixtures are
    marked synthetic. New Recruit writes a unit's models and a unit's wargear
    at the same bullet depth:

        Boyz (180 Points)          Warboss (65 Points)
          • 19x Ork Boy              • 1x Attack squig
             ◦ 19x Choppa            • 1x Kombi-weapon
          • 1x Boss Nob              • 1x Power klaw
             ◦ 1x Power klaw

    Twenty models on the left, one on the right, and nothing in the text says
    which is which — `1x Boss Nob` and `1x Power klaw` are the same shape.

    What does separate them is nesting. Wargear hangs off a model, so when a
    block nests at all, the outermost bullets are models and the deeper ones
    are their weapons. When nothing nests, the block is one model's kit and the
    unit is a single model.

    A flat multi-model block — models listed with no wargear under them — reads
    as 1 here and is the known false negative. It is the safe direction to be
    wrong in *for a list*, where the resolved datasheet's `min_models` catches
    it in the report rather than sending anyone shopping.

    It is **not** safe for `/add`, which writes the models. The 2000-point list
    in `fixtures/lists/pasted_orks_2000.txt` is flat from top to bottom: twenty
    units, ninety-two models, and every one of them read as 1.

    `uncounted_wargear` is what rescues it. **The signal is unverified**, and
    honestly so: that fixture is text Clay pasted, and he said afterwards it came
    out of a Claude conversation rather than out of an app. It is a real input —
    he really does paste model-written lists here — and it is no evidence at all
    about what New Recruit or the GW app actually writes. Nothing in this repo
    has ever read a verified export. In that file a model bullet always carries
    a count and a wargear bullet never does:

        Flash Gitz (170 points)        Boyz (75 points)
          • 5x Flash Git    <- models    • 10x Ork Boy   <- models
          • Supa Snazz-Dakka <- wargear

    Where New Recruit counts *everything* and separates models from wargear by
    nesting alone:

        Warboss (65 Points)            Boyz (180 Points)
          • 1x Attack squig             • 19x Ork Boy
          • 1x Kombi-weapon                ◦ 19x Choppa
          • 1x Power klaw               • 1x Boss Nob

    So when a document contains any uncounted bullet, its convention is taken to
    be the first one, a counted bullet is a model, and a flat block is summed.
    When every bullet in the document is counted, nothing has changed and the
    flat case still reads 1 — `synthetic_newrecruit_flat.txt` is genuinely
    ambiguous and must stay that way.

    The rule is conservative rather than proven: it fires only on positive
    evidence of the uncounted convention, and it is wrong if a real export ever
    mixes the two. A real sample would settle it, and would be the first.

    Decided per document, never per block: `Boyz` with one `• 10x Ork Boy` is
    identical in both conventions, and only the rest of the file says which it
    is written in.
    """
    depths = {}
    for line in bullets:
        found = _BULLET.match(line)
        counted = _COUNTED.match(found.group('body').strip())
        if not counted:
            continue
        depth = len(found.group('indent'))
        depths.setdefault(depth, []).append(int(counted.group('count')))
    if not depths:
        return 1
    if len(depths) == 1 and not uncounted_wargear:
        return 1
    return sum(depths[min(depths)])


def _parse_newrecruit(lines):
    entries = []
    uncounted = _uncounted_wargear(lines)
    for header, bullets in _blocks(lines):
        found = _NAMED_POINTS.match(header)
        name = found.group('name').strip() if found else header
        points = int(found.group('points')) if found else None
        entries.append(ParsedEntry(
            name, _newrecruit_count(bullets, uncounted), points, len(entries)))
    return entries


def _parse_gw_app(lines):
    entries = []
    for header, _bullets in _blocks(lines):
        found = _GW_ENTRY.match(header)
        if found:
            entries.append(ParsedEntry(
                found.group('name').strip(), int(found.group('count')),
                int(found.group('points')), len(entries)))
            continue
        # Characters carry no count in this format, and the fallback below
        # keeps anything else visible rather than dropping it.
        named = _NAMED_POINTS.match(header)
        if named:
            entries.append(ParsedEntry(named.group('name').strip(), 1,
                                       int(named.group('points')),
                                       len(entries)))
        else:
            entries.append(ParsedEntry(header, 1, None, len(entries)))
    return entries


def _parse_permissive(lines):
    """Anything else: find a count and a points figure wherever they sit.

    The last resort, and the one that has to be hardest to lose a line in. A
    line that matches nothing at all still becomes an entry — the whole line as
    its name, for Clay to point at a datasheet.
    """
    entries = []
    for header, bullets in _blocks(lines):
        entries.append(_permissive_entry(header, bullets, len(entries)))
    return entries


def _permissive_entry(line, bullets, position):
    gw = _GW_ENTRY.match(line)
    if gw:
        return ParsedEntry(gw.group('name').strip(), int(gw.group('count')),
                           int(gw.group('points')), position)

    points = None
    named = _NAMED_POINTS.match(line)
    if named:
        line, points = named.group('name').strip(), int(named.group('points'))
    else:
        # A retyped sheet writes them without brackets: "Warboss - 65",
        # "Boyz x20 - 180". Same annotation the collection paste already reads,
        # so it is read with the same pattern rather than a second one.
        stripped = POINTS_RE.sub('', line)
        if stripped and stripped != line and not stripped.strip().isdigit():
            found = re.search(r'(\d+)', line[len(stripped):])
            points = int(found.group(1)) if found else None
            line = stripped.strip()

    count = 1
    leading = re.match(r'^(\d+)\s*[x×]?\s+(.*)$', line, re.IGNORECASE)
    trailing = re.match(r'^(.*?)\s*[x×]\s*(\d+)$', line, re.IGNORECASE)
    if leading:
        count, line = int(leading.group(1)), leading.group(2)
    elif trailing:
        line, count = trailing.group(1), int(trailing.group(2))
    elif bullets:
        # No count on the header, but the detail underneath may carry one.
        count = _newrecruit_count(bullets)

    name = line.strip().rstrip('.,;:-–—')
    return ParsedEntry(name or line.strip(), max(1, count), points, position)
