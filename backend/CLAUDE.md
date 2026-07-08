# backend/ — BigQuery SQL source (Differential Histograms)

Deployable SQL for the BigQuery objects the notebooks depend on, extracted
verbatim from `mlab-collaboration.mm_preproduction` so **git is the source of
truth** and BigQuery is a deployment target (the `bq-tools` skill method).

> **Terminology** (in progress — see `REDESIGN.md`): this collection is
> **"Differential Histograms"**; the word "fleet" is being reserved for M-Lab's
> fleet of servers. Some existing text here and in `README.md` still says "fleet".

## Layout

- `routines/` — TABLE FUNCTIONs (histogram + report logic)
- `views/`, `tables/` — views; table schema DDL only (data lives in BQ)
- `scheduled/` — transfer-config SQL (the weekly cache builder) + schedule
- `MANIFEST.json` — object types, dependency edges, deploy order, scheduled queries
- `README.md` — human-facing detail: inventory, deploy order, round-trip check, limitations
- `REDESIGN.md` — the v3 backend design (star schema, weekly caches, Python
  `method` interface). **Currently paused.** Contains `TODO:`/`OPEN:` markers for
  the sections still to expand — grep `` grep -nE '`(TODO|OPEN):' backend/REDESIGN.md ``.

## Extraction / round-trip (`tools/extract_backend.py`)

`python tools/extract_backend.py --out backend` re-extracts the fleet: it reads
the authoritative `CREATE …` text from `INFORMATION_SCHEMA.…ddl` and walks the
dependency closure from the notebook-facing routines (`ENTRY`) plus the tracked
scheduled queries (`SCHEDULED`). It is deterministic — re-extract and
`diff -r --exclude=README.md backend /tmp/verify` should be clean (a diff is a
finding).

Run it where `bq` is authenticated. The **annealing service account** is the
reliable path; the local user token lapses under the measurementlab.net
Workspace reauth policy.

## Working notes

- Dependency edges in `MANIFEST.json` are a **heuristic text scan** — they include
  names that appear only in comments or `ERROR('…')` strings (e.g. a phantom
  `experimental ↔ unified` cycle) and miss wildcard / dynamically-built reads.
  The extracted SQL is truth; the edge graph is an approximate map.
- The raw-ingest boundary tables (`*_DS16`, `*_DS1*`, `extended_*`) are inputs
  produced by a separate project — out of scope, likely prune candidates (README).
- Known backend issue to fix in the v3 rework: `unified_ndt7_isp_histograms`
  selects its source by DS modifier, so bare `method=live` returns 0 rows while
  `live-DS16` works.
