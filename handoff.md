# Handoff — Session 6

## 1 · Goal

Deploy to bastion. Everything in this session came out of doing that on the
real box rather than reading about it.

## 2 · Current State

**The app is live on bastion, with rules data loaded and a verified backup.**
Five PRs merged (#8–#12), every one of them a bug the deploy found.

| | |
|---|---|
| Image | `6a354aa88431`, healthy on port 3100 |
| Datasheets | 1,445 (+330 Legends, +62 Crucible, kept and flagged) |
| Points rows | 2,544, of which 399 inherited Chapter listings |
| Factions | 30 |
| Unresolved | 31 — the documented figure, see below |
| Collection | empty. Nothing scanned yet. |
| Backup | `tracker-20260821-110536.db`, 839,680 bytes, verified restorable |

The 31 unresolved rows are all expected classes, none of them a matching
failure: naming variants between the two sources (MFM "Vyper" / BSData
"Vypers", one Soul Grinder against four god-specific ones, Blight-Haulers vs
Blight-hauler); Legion units priced under their own faction rather than
`chaos-space-marines` (Berzerkers, Rubric/Plague/Noise Marines) — the faction
scoping doing exactly its job; units with no points cost at all (Spore Mines,
Mucolid Spores, Ripper Swarms attached to a Parasite); the four Chaos Titans,
priced in a catalogue outside `wh40k-11e`; and one true ambiguity, **Wolf Guard
Headtakers**, where two datasheets share a name inside Space Wolves.

Three named Space Marine characters — **Captain Sicarius, Marneus Calgar,
Lieutenant Titus** — have no MFM entry and so no points. Only matters at list
building, which is step 8 and out of scope.

**Not yet done:** the Cloudflare Tunnel is not pointed at 3100, so the camera
has never been tested against a real box in real light. That is the one part of
this app that has to work somewhere other than a desk.

## 3 · Active Files

- `Dockerfile` — now installs `git`
- `deploy.sh` — rules-data step rewritten
- `scripts/fetch_bsdata.py` — `git()` helper, `current_sha()` returns errors
- `restore.sh` — the empty-collection check
- `.gitignore`, `tests/test_gitignore.py`
- `tests/test_fetch_bsdata.py` (new), `tests/test_backup.py` (two tests replaced)
- `.github/workflows/ci.yml` — image tooling check
- `docker-compose.yml` — `version: '3.3'`

## 4 · Changes Made

**#8 — compose schema.** `version: '3.8'` was rejected outright by bastion's
docker-compose. 3.3 works on both v1 and v2. `healthcheck.start_period` dropped
with it (needs 3.4).

**#9 — git in the image.** `python:3.11-slim` has no git, so
`fetch_bsdata.py` died on a fresh box after the container had already booted
healthy. git rather than a tarball because the script fetches one pinned commit
and verifies it got that commit. CI now runs the setup scripts' tools against
the built image — the docker job booted the container and called `/healthz`,
which this sails straight past.

**#10 — two gates.** `.gitignore` covered `.env` and none of nano's copies
(`.env.save`, `.env.save.1`, `..env.swp`), each holding SESSION_SECRET and
OWNER_PASSWORD. And `deploy.sh` gated the import on the *checkout directory*
existing, so a fetch without an import left the app with no datasheets and a
deploy script convinced there was nothing to do. Now gated on
`count(*) from datasheets`, printed every deploy.

**#11 — the refusal that destroyed data.** `fetch_bsdata.py` reported a
complete 46-catalogue checkout as "not a BSData checkout. Move it aside." The
checkout was owned by the host user; container git runs as root and refuses
another user's repository. `current_sha()` sent that to DEVNULL and returned
None, and None meant one thing at the call site. Every git call is now scoped
with `safe.directory`; `current_sha()` returns `(sha, error)`; the refusal
counts catalogues and says *do not move or delete it*.

**#12 — crying wolf on day one.** `restore.sh --check` failed a perfectly good
first snapshot because it held no collection — there is no collection yet. Now
compared against the live database: empty snapshot behind populated live still
fails and says so; empty behind empty is a fresh install; unreadable live is
reported as unverified.

225 tests pass. shellcheck clean on all three scripts.

## 5 · Failed Attempts

**I told Clay to delete a good checkout.** He hit "not a BSData checkout",
I diagnosed a partial clone from a failed `git init`, and told him to
`mv data/bsdata ~/bsdata.broken`. It was a complete 46-catalogue checkout; the
only reason it still exists is that he ran `ls` first, as suggested, instead of
the `mv`. The script's own message backed the wrong diagnosis, which is what
#11 fixes — but the guess was mine, made without asking for evidence I had
already asked for.

**I said "git pull && ./deploy.sh" while the fix sat in an unmerged PR.** Twice.
The tell both times was every layer reporting `Using cache` and the build ending
on the identical image ID `6a45dfe2ea0c` — visible in the screenshot I had, and
not read. The second time it also failed because of a local `sed` edit *I* had
told him to make on `docker-compose.yml`, which blocked the pull.

**I quoted "~1,900 datasheets" as the health checkpoint.** The real figure is
1,445, which I had in the module docstring and did not check before giving him
a threshold to judge his own deploy against.

**Pattern across all three:** guessing when a one-line command would have
produced the answer, on a box I cannot see and he can. The screenshots carried
what was needed every time.

## 6 · Next Steps

**Point the Cloudflare Tunnel at 3100 and scan a real box.** `getUserMedia`
needs the HTTPS origin; a plain-HTTP Tailscale IP will fail silently on every
iOS browser. Nothing about the scanner has been exercised on real hardware in
real light, and that is where it has to work.

**Then the collection view** — the other half of step 5, the "do I already own
one of these" screen, and the last thing before v1 is done. No blockers.

**Still open, unchanged:**

- `BACKUP_DEST` unset. Snapshots land on `/mnt/t7` — a different disk, same
  box. The backup script says so itself: *one box is not a backup.*
- The **90 Combat Patrol issues** still need a source: an egress allowance for
  `fauxhammer.com` / `hachettepartworks.com`, or Clay pasting the list.
  `seed/data/README.md` has the format; `--dry-run` reports mismatches.
- **Two premium-kit decisions**: which Daemon Prince variant, and whether GSC
  Broodcoven splits into Magus / Primus / Patriarch.
- The **kit catalogue seed job** (§11) — same egress problem, worse.
- Dependencies unpinned (`>=`), so CI resolves the latest release each run.
- **Wolf Guard Headtakers** needs a pick between two same-named datasheets.

**Do not build past step 5.**
