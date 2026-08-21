# Handoff — Session 5

## 1 · Goal

Build the Combat Patrol magazine templates, all 90 issues (§11).

## 2 · Current State

**The machinery is built, tested and working. The contents are not in it, and
I did not put them there.**

`seed/combat_patrol_magazine.py` reads a contents file, matches every unit
against the imported BSData datasheets, creates one kit template per issue, and
optionally instantiates owned kits up to a given issue. 29 tests cover it; 193
pass overall.

`seed/data/combat_patrol_issues.yaml` **ships empty of issue data**.

### Why it is empty

Every published source for the per-issue contents is blocked by this
environment's egress policy:

| Source | Result |
|---|---|
| `fauxhammer.com` (a contents list covering all 90) | `EGRESS_BLOCKED` |
| `hachettepartworks.com` (the publisher's own per-issue pages) | `EGRESS_BLOCKED` |
| `warhammer-community.com` | `EGRESS_BLOCKED` |
| `hachettecollections.com`, `bolterandchainsword.com`, Wikipedia | all blocked |

`/root/.ccr/README.md` is explicit: a 403 from the proxy is organization egress
policy, and the instruction is to report the blocked host, not route around it.

`WebSearch` *does* work, and returned real fragments — issue 41 a Warboss in
Mega Armour, 43 and 45 ten Ork Boyz each, 47–48 Deffkoptas, 49–50 a Deff Dread,
59 a Boomdakka Snazzwagon. **I deliberately did not use them.** They arrive as
a model's summary of a page I cannot read, they cover six issues of ninety, and
nothing corroborates them. Assembling 90 issues that way produces exactly what
§11 forbids: fluent, plausible, wrong in places, with no signal about which —
and it would land as trusted seed data covering the entire magazine collection,
where nothing would ever prompt Clay to check it.

So the code is done and the data is a one-file drop-in.

## 3 · Active Files

| File | Role |
|---|---|
| `seed/combat_patrol_magazine.py` | The seed job |
| `seed/data/combat_patrol_issues.yaml` | Contents + provenance; empty of issues |
| `seed/data/README.md` | Where to get the data, and the rules for filling it |
| `tests/test_combat_patrol_seed.py` | 29 tests |

## 4 · Changes Made

**Provenance is enforced, not requested.** The importer refuses to run without
source URLs, a retrieval date, a confidence, and a second source that agrees.
Undated, unattributed seed data is indistinguishable from invented seed data
once it is in the database, and every template it creates carries
`contents_source='seed'` plus the URLs.

`--dry-run` deliberately works *without* provenance, so a half-finished draft
can still be checked.

**Multi-issue sprues attach to the issue that completes them.** A Maulerfiend
split across #89–90 becomes one template on #90; #89 is reported as parts-only
and yields no kit. Half a Maulerfiend is not a model you own.

**Nothing is invented to fill a gap.** Unmatched unit names become
`unresolved_imports` rows and appear in the report. An issue whose units all
fail to match produces *no* template, because an empty template instantiates an
empty kit and looks like it worked.

**Matching reuses the rules importer's `norm`**, so a name that matched there
matches here. Legends and Crucible variants are excluded — a deprecated
printing must never satisfy a seed.

**The nine Combat Patrols and four premium kits are shipped**, because those
come from the spec itself rather than from a web source.

### Demonstrated against real data

A throwaway fixture (in scratch, not committed) run against the real 1,445
datasheets: 7 issues in, 5 templates out, 4 owned kits and 22 models for issues
≤75, the Maulerfiend correctly on #90, a deliberately fake unit reported rather
than invented, and the 83 missing issues printed as compact ranges. `--dry-run`
wrote nothing.

## 5 · Failed Attempts

**Reaching a source, five different ways.** `curl` to the publisher and the
community lists: all `000` through the proxy. `WebFetch` to the same: all
`EGRESS_BLOCKED`. Only `WebSearch` — which returns summaries rather than pages
— gets out.

**Assembling the list from search snippets.** Tempting, and it *would* have
produced ninety plausible rows. Rejected: single-source, second-hand through a
summariser, six issues of ninety actually covered, and impossible to verify
line by line. This is the failure mode §11 exists to prevent, and it would have
been worse here than for the kit catalogue because the magazine seed is
imported wholesale as trusted.

**A test fixture that held an open write transaction** deadlocked the tests
that go through `main()`, which opens its own connection — `database is
locked`. The fixture now commits.

## 6 · Next Steps

**To finish this task, the data needs to get in.** Either:

1. **Allow one host through egress** — `fauxhammer.com` has a list covering all
   90 in one page, `hachettepartworks.com` is the publisher. Then I can derive,
   corroborate and fill the file properly in one pass.
2. **Paste or drop the list in.** `seed/data/README.md` documents the format;
   `--status` and `--dry-run` will tell you immediately what does not match.

Then `python3 seed/combat_patrol_magazine.py --owned-through 75`.

**Also still open:**

- The **kit catalogue seed job** (§11) has the same problem and worse — it needs
  to walk retailer pages for EANs and GW's verbatim contents block. Not runnable
  from this environment at all as things stand.
- The **collection view** is the other half of step 5 and has no such blocker.
  It is the "do I already own one of these" screen, and the last thing before
  v1 is done.
- `BACKUP_DEST` is still unset — backups are local-only.
- Dependencies are unpinned, so CI resolves the latest release each run.

**Do not build past step 5.**
