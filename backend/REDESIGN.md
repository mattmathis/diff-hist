# Differential Histograms — Backend Redesign

This document describes changes to the Differential Histogram backend to improve code maintainability, experimental agility and performance.   For the most part they are not deep structural changes.

claude --resume fa7665aa-498d-41dc-bfde-25e01764f7b8

## Goals / Non-goals

Goals:

 - Improve the code maintainability and experimental agility.   Most of the problems with the current backend are relatively minor, but difficult to correct when they require parallel edits to multiple files.

 - Improve efficiency across all implementations by avoiding excess BigQuery reads and network transfers  between BigQuery and the applications, by conversion to a star schema.  The current implementation results in unnecessary repetition of data over expensive access paths.

 - Support easy prototyping of experimental features.

 - Facilitate porting the histogram-based applications to other technologies in addition to Grafana and Jupyter, for example applications written in JavaScript based on fetching Parquet files from GCS.

- Preserve most of the basic architecture of the current (version 1) notebooks.  Most of the changes here are specified incrementally relative to the existing code.

- The related [data-presentation project](https://github.com/mattmathis/data-presentation) is prototyping better ways of publishing M-Lab data.   The `*_DS16`/`*_DS1`
and `extended_*` inputs are part of that project, and out of scope here.  The Data Selection layer may include source table selectors (based on sub-selectors of the method selector) to exercise or work around different data-presentation implementations.


## Current state & problems

 The old histograms were inefficient because the axes contain parallel redundant information, such as the clients' and servers' full AS names. The storage and I/O cost are needlessly multiplied by other dimensions in most storage architectures. Correct this inefficiency by splitting this information  into a star schema, and updating the applications to join with it later in the processing.

The data caches are quite configurable, explicitly supporting null out dimensions.  Many of the complexity vs cost tradeoffs can best be explored during prototyping.

## Terminology

Refer to this collection of tools as "Differential Histograms", even though a few of the items don't use histograms.

We carelessly use cache to meant two different things.  Unless clear from context use:
- Server cache -- keeping precomputed histograms in BQ tables or other  relatively long term data storage;
- Memory cache -- keeping histograms in client memory, for displaying multiple views of the same data without re-fetching it from the server cache.

Please point out ambiguous usage.

Reserve the term "fleet" for "MLab's fleet of servers".

`TODO:` As part of the plan sweep this rename through the repo — `backend/README.md`,
`MANIFEST.json` wording, `tools/extract_backend.py`, and the `bq-tools` skill
currently use "fleet" for the SQL collection.

## Architecture

Produce differential measurements plots and reports from M-Lab NDT7 data in BQ using an efficient intermediate histogram representations.

CLAUDE: The specifications here are thin and terse; many missing details can be reconstructed from the existing code.

### Multiple Parallel Implementations (Silos)
Use parallel implementations (silos) of equivalent processing stacks that perform similar algorithms in the same sequence and locations, but supporting progressively richer analyses.

The silos are:
- Precomputed server caches, indexed by week. Initially just BQ tables. In the future these will include alternate cache implementations (e.g. parquet files in GCS).  Other than interposing the cache itself, this should be identical to live access, and auditable against it.
- Live access, where the plots and reports use histograms that are computed directly from BQ.   This will be the reference implementation.  Note that it is likely to support more options than the cached version.
- Experimental, same as live but with the addition of non-standard experimental features. To enhance experimental agility, support multiple independent experimental implementations.

It will be important to keep the silos aligned by copying code between them.

### Processing Layers
The processing silos all have the same stages with common interfaces:
- Data selection: select input table(s), metric column, and labeling for dedupe 
- Compute histograms: dedupe filtering, transform metrics to bins, (CLAUDE: move auxiliary labels to separate dimension tables)
- Optionally write server caches
- Histogram access and normalization: source selection (in principle connect andy frontend to any backend), zero fill (densification), convert bins back to metrics, load the memory cache, and link to (join?) dimension tables
- Reports (tabular output): simple or specialized analysis algorithms based on the memory cache.  For existing key reports in BQ also update them to use the new backend, as calibration references. 
- Plots: Keep a library of common elements: stacked cdf/pdf, tables with breadcrumb links and non-scrolled titles

### Access and parameter passing

Assume one primary access function (replaces BQ access* TVFs), with a standard calling sequence that controls all of the processing in the stack.   The common control philosophy will be to uses the presence of substrings in the parameters (mostly named *SX) to select granularity or other transforms along that axis

Parameters:
- sourceSX (was method): access source (which processing silo) and data source
- metricSX (was field): also controls the transform to bins and back 
- timeSX, (was xAxis) time granularity and other time transforms (e.g. diurnal)
- EndDate, duration: Common date picker
- serverRegex (was metro)
- ispCount (see below)
- ispRegex
- labelSX - Processing options for client metadata and other labels
- 

### The ISP count optimization

### Cache Structure
Instead of a single cache, keep weekly cached results in separate files/tables, named by week and metric.  In the future this will support both summing data over multiple weeks and week granularity timeseries. 

Assume the existence of multiple cache types and experimental backends, all selected by the sourceSX parameter to a common interface that can access all of the caches, live and experimental backends

`OPEN:` metro-level projections — materialized tables **or** computed in Python
at query time? (The sketch says both; see Schemas → "Metro-level summary
projections". This choice drives much of the rest.)

`OPEN:` fate of the existing report TVFs (`regional_report`, `calibration_report`,
`minRTT_competition_report`, `throughput_competition_report`, `metro_report`,
`cached_metro_report`), which today do the metro projections in SQL — reimplement
in Python, or keep as SQL? (Follows from the projection decision.)

## Data structures / Schemas

### Main histogram (fact table)

The main output of both the old and new are multi-dimensional histogram with the following primary axis:
- Metric under investigation
- MLab site
- The Client's ASnumber
- The Client's rank in the metro (used for BQ clustering and client selection)
- The client's Geo information (new, but the cost needs to be evaluated)
- TimeBin (Constant is the current tables, but supports timeseries and diurnal analysis)
- Bin Index
- and the count

`TODO:` concrete column schema + types. Which columns stay in the fact table as
keys vs which are joined in from dimensions.

`TODO:` partition key (week = TimeBin?) and cluster keys (rank + ?). Note the
`require_partition_filter` implication for the Python accessor (every read must
supply a week filter).

`TODO:` enumerate which current cache columns are **dropped** from the fact and
where each lands — `ISPname`, `siteName`, `metroRawTests`, `metroTests`,
`metroStart`, `metroEnd` (this mapping is the storage/IO win; quantify it).

### Client metadata (dimension)

Add a separate client metadata table, that maps client ASnumbers to ASnames.  This data currently comes from annotations on NDT data.  We may augment it with data from public databases.  These should be generated weekly but assume that the most recent one is fine for looking at past data.

`TODO:` schema + types.

`TODO:` confirm the "most recent is fine for past data" rule = Type-1
current-snapshot join (no point-in-time history).

`OPEN:` client Geo information ("new, but the cost needs to be evaluated") —
include it here or not? State the cost gate / decision criterion.

### Server metadata (dimension)

The existing server metadata is close to what we need.   We may add some new sources of information and change how it builds over time, but not make significant architectural changes.  One known missing item is the serving probability used by the locate service.

`TODO:` enumerate the exact delta vs the current `server_metadata` (serving
probability from Locate; anything else) rather than "may add some".

### Client selection (dimension)

Add a separate client selection table corresponding to the existing subquery ndt7_ISPs.  This is used to populate the clients dropdown for a given anchor metro.   Also generated weekly.
(source today: the `ndt7_ISPs` CTE in `routines/experimental_ndt7_isp_histograms.sql`)

`TODO:` schema (metro, week, ASnumber, ISPrank, counts?).

`TODO:` pin down how `ISPrank` is computed, and require it to match the fact
table's rank exactly — clustering and client selection both depend on it, so
they must agree.

### Metro-level summary projections

Add a summary table that is a projection of the main histogram to eliminate the server dimension within a metro.   i.e. for each ISP within a metro get the histograms of each client ISP to all servers in the metro.
(No do this in python)

Add a summary table that is a projection of the main histogram to eliminate the client dimension within a metro.  i.e. for each server get the histograms of all of the traffic to each server, including traffic from clients that are out of region
(no do this in python)

`OPEN:` the "(No do this in python)" annotations conflict with "Add a summary
table" — decide materialized-table vs Python-at-query-time and state it once,
with the reason. (Also resolves the report-TVF `OPEN:` above.)

## The method interface (Python access layer)

Replace all of access_ndt7_cached_histograms.sql with python that accesses caches, live data or experimental queries, 
.  There should not be any "Common metadata" passed 

`TODO:` specify the contract: the `method` grammar (backend token + modifiers,
e.g. today's `cached`/`live`/`exp` + `DS*`), how week selection is parameterized
(most-recent / a specific week / a range for timeseries), the future multi-week
summing semantics, and the **uniform return schema** every backend must produce
so the notebooks stay backend-agnostic.

## Scope & object disposition

The following BQ objects are out of scope for this design.  If they are used, they should be documented as inputs.  After reviewing the dependency closure, things that are not used they should be removed entirely.
- All of the views extended_\*
- All of the tables \*_DS16.sql and \*_DS1.sql
- All of the routines to manage, test or update the items above

`TODO:` draw the input boundary precisely — `get_ndt7_data` and
`experimental_ndt7_isp_histograms` *read from* the objects above; state whether
each is an input we keep/document or logic we reimplement.

`TODO:` a table mapping every current `backend/` object → keep-as-input /
reimplement / remove. Use `MANIFEST.json` dependency edges to find the unused
ones (mind the heuristic-edge caveat — comment/string false positives, wildcard
false negatives).

## Migration & rollout plan

`TODO:` phased steps — build the new tables alongside the old; cut the Python
accessor over per `method`; backfill the weekly caches; deprecate the old
objects; and the rollback path at each phase.

## Validation & cost

`TODO:` golden-output regression — the normalized+rejoined result must equal
today's denormalized cache for `method=cached` on a fixed input slice (the check
that de-risks a data-model change).

`TODO:` before/after bytes-scanned and table sizes (cost is the stated
motivation, and the gate for the client-geo columns).

## Open questions (rollup)

- `OPEN:` metro projections — materialized tables vs computed in Python.
- `OPEN:` fate of the existing report TVFs (reimplement in Python vs keep SQL).
- `OPEN:` include client Geo columns? (cost-gated)
- `OPEN:` `ISPrank` definition, kept consistent between fact table and selection table.
- `OPEN:` input boundary for `get_ndt7_data` / `experimental_ndt7_isp_histograms`.
