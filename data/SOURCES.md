# Vendored rules data

## `data/mfm/` — points (vendored, committed)

Snapshots of the official [Munitorum Field Manual](https://mfm.warhammer-community.com/en)
parsed by [BSData/wh40k-11e-mfm](https://github.com/BSData/wh40k-11e-mfm).

| | |
|---|---|
| Pinned commit | `06754e2f2e0e9c2b3f7fe46b2a96972702f43f22` |
| MFM version | 1.2 (`lastUpdated: 2026-08-05`) |
| Licence | MIT — see `data/mfm/LICENSE` |

MIT-licensed and 600 KB, so it is committed directly. This is the **points**
source; see the note in `scripts/import_bsdata.py` for why it beats flattening
BSData's cost modifiers.

## `data/bsdata/` — datasheets (fetched, gitignored)

[BSData/wh40k-11e](https://github.com/BSData/wh40k-11e), pinned to
`13f3c4e54d15f96baebdc48c3a8c10431db2990f`.

Fetch it with:

```bash
python3 scripts/fetch_bsdata.py
```

**Not committed**, for two reasons. It is 65 MB of JSON, which would sit in this
repo's history forever. And `wh40k-11e` ships **no licence file** — its README
states it is community-maintained and not endorsed by any publisher. The spec's
rule is "fine for a private single-user app on Clay's own hardware; do not
redistribute the data or publish the app publicly with it baked in", and the
cleanest way to honour that is to not bake it in at all.

The pin still gives what vendoring was for: the fetch is reproducible, the
import is deterministic, and nothing is fetched at runtime — `fetch_bsdata.py`
is a setup step, and the app only ever reads what the importer already wrote
into SQLite.
