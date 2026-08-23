# List export samples

**Every file in this directory is SYNTHETIC.** Not one is a real export.

They are written from the documented shape of each format, because every
candidate host for a real New Recruit or GW app export is refused by this
environment's egress policy — the same wall that kept spec §2.7 marked "blocked
on a source" for weeks. Pasting is the door that always works, and it is also
the only way a *real* sample gets in here.

So: **replace these.** Export a list from each app, paste it into a file, drop
the `synthetic_` prefix, and the tests will read it. The ones most worth
replacing first are the New Recruit files, because `list_parse._newrecruit_count`
infers a unit's model count from bullet nesting and that rule is the single
thing in the parser most likely to be wrong.

`unknown_*.txt` are deliberately not any format — a chat message, a retyped
sheet, a wargear-only fragment. Those are real shapes even though the text is
invented, and they are what the permissive fallback exists for.
