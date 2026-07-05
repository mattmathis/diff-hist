# BigQuery backend source (`mlab-collaboration.mm_preproduction`)

Deployable SQL for the differential-performance **fleet** — the BigQuery
routines, views, and tables the notebooks depend on. Extracted verbatim from
the live dataset so **git is the source of truth** and the warehouse is a
deployment target (see the `sql-fleet` method). This is the *baseline
extraction* (lifecycle step 1): the repo now matches reality, so future changes
are reviewable diffs.

> **First pass.** The SQL is authoritative and redeployable; the dependency
> graph and deploy order are an approximate overlay (see Limitations).

## How it was produced

```bash
# Run where bq is authenticated (local, or the annealing SA).
python tools/extract_backend.py --out backend
```

`tools/extract_backend.py` reads the authoritative `CREATE …` text from
`INFORMATION_SCHEMA.{ROUTINES,TABLES}.ddl`, then walks the **transitive
dependency closure** starting from the notebook-facing routines (`ENTRY`) plus
the objects referenced by the tracked scheduled queries (`SCHEDULED`) — so it
captures the whole fleet and skips the dataset's unrelated historical/
exploratory objects (88 objects in the dataset → 29 in the fleet + 1 scheduled
query).

## Layout

```
backend/
├── routines/     17 TABLE FUNCTIONs (the histogram + report logic)
├── views/         4 VIEWs (extended_ndt7_*, *_DS1V)
├── tables/        8 BASE TABLEs — schema DDL only (data lives in BQ)
├── scheduled/     1 scheduled query — the weekly cache builder (SQL + schedule)
└── MANIFEST.json  types, dependency edges, deploy order, scheduled queries
```

Each `.sql` is a self-contained `CREATE … ` (idempotent to redeploy with
`CREATE OR REPLACE`). Table files carry schema DDL only — they are data outputs.
`scheduled/` holds the transfer-config SQL verbatim with a `-- schedule:` header.

## The fleet

**Notebook-facing TVFs** (the `ENTRY` set): `access_ndt7_cached_histograms`,
`access_ndt7_isp_histograms`, `access_exp_ndt7_isp_histograms`,
`experimental_ndt7_isp_histograms`, `unified_ndt7_isp_histograms`,
`regional_report`, `calibration_report`, `minRTT_competition_report`,
`throughput_competition_report`, `global_fleet_inventory`, `cached_metro_report`.

**Support TVFs**: `get_ndt7_data`, `extended_intermediate_{downloads,uploads}_DS16`,
`metro_report`, `timeseries_report`, `server_metadata`.

**Views**: `extended_ndt7_{downloads,uploads}`,
`extended_intermediate_{downloads,uploads}_DS1V`.

**Scheduled query**: `scheduled/cached_ndt7_isp_histograms.sql` — the weekly
(`every sun 23:00`) job that **builds the histogram cache**: five `CREATE OR
REPLACE TABLE` statements writing the `cached_ndt7_isp_histogram_download_*`
tables from `unified_ndt7_isp_histograms` + `server_metadata`/`cached_metadata`.
This is the *write* side of `access_ndt7_cached_histograms` (the *read* side).
It lives in `mlab-collaboration` — part of this project.

**Histogram cache tables (written by that weekly job)**:
`cached_ndt7_isp_histogram_download_{MinRTT,MinRTT_linear,Throughput,LossRatio}`
— schema only (the data is rebuilt weekly). `access_ndt7_cached_histograms`
reads them via the wildcard `cached_ndt7_isp_histogram*`.

**Raw-ingest boundary tables (schema only, likely prune candidates)**:
`autoload_DS16`, `ndt7_DS16`, `cached_metadata`,
`extended_intermediate_downloads_DS1C`. Upstream data inputs whose **producing
jobs are out of scope** (the two daily `Update *_DS16` scheduled queries read
`measurement-lab` source data and are intentionally not extracted). The
extraction stops at their schema; their producers and deeper deps are not chased.

Deploy order (dependencies first) is in `MANIFEST.json → deploy_order`; deploy
with `CREATE OR REPLACE` in that order.

## Known limitations (to refine in later passes)

- **Heuristic dependency edges.** Edges come from a whole-word scan of each
  object's DDL, so they include names mentioned only in comments or `ERROR('…')`
  strings. The apparent `experimental_ndt7_isp_histograms ↔
  unified_ndt7_isp_histograms` **cycle is not real** — those are comment/error
  mentions, not calls.
- **Wildcard / dynamic reads under-detected in the edge graph.**
  `access_ndt7_cached_histograms` reads `cached_ndt7_isp_histogram*` (a wildcard
  table filtered by tag), so those cache tables don't appear as *edges* of the
  access TVF. They are nonetheless extracted — seeded into the closure by the
  weekly `cached_ndt7_isp_histograms` scheduled query that writes them.
- **Tables are schema-only.** No data. Raw-ingest boundary tables
  (`autoload_DS16`, `ndt7_DS16`, …) are populated by the daily `Update *_DS16`
  scheduled queries, which read `measurement-lab` source data and are
  intentionally **out of scope** (not extracted). Only the weekly cache builder
  is tracked. These boundary table files are likely prune candidates.
- **Single environment.** These target `mm_preproduction` directly; no dev/prod
  dataset separation yet.

## Known backend issue

`unified_ndt7_isp_histograms` selects its source by the downsampling modifier
(`extended_intermediate_*_DS16`/`_DS1V`). Bare `method=live` (no DS modifier)
returns **0 rows**; `live-DS16` works. This is a backend behaviour to address on
this branch — the notebook conversion code is correct.

## Round-trip check

Re-extract and diff to prove `deployed == committed` (sql-fleet step 6):

```bash
python tools/extract_backend.py --out /tmp/backend-verify
diff -r --exclude=README.md backend /tmp/backend-verify   # any diff is a finding
```

See also `docs/functions/` — human-readable reference docs (partial; generated
by `tools/gen_docs.py`). `backend/` is the deployable source of truth.
