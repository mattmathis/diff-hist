# Grafana → Jupyter Notebook Converter

## Project Goal

Convert a collection of Grafana dashboards (JSON format) into interactive Jupyter
notebooks, with a "webapp" mode that accepts dropdown presets from URL parameters.

> **Datasource:** These dashboards query **Google BigQuery** directly via the
> `grafana-bigquery-datasource` plugin. Each panel target carries a `rawSql`
> string (BigQuery Standard SQL), **not** PromQL/Prometheus. The SQL calls BigQuery
> *table functions* and *tables* in the `mlab-collaboration.mm_preproduction`
> dataset, documented under `docs/functions/`. The `bq` CLI is authenticated as
> `mattmathis@measurementlab.net` (project `mlab-collaboration`).

## Stack

| Purpose | Library |
|---|---|
| Dropdowns | `ipywidgets` |
| Charts | `plotly` |
| Data queries | `google-cloud-bigquery` |
| Dataframes | `pandas` |
| Webapp serving | Voilà |
| URL param presets | Voilà `get_query_string()` (reads `QUERY_STRING` env var) |

## Project Structure

```
project/
├── CLAUDE.md
├── converter/
│   ├── parser.py           # Parse Grafana JSON → Dashboard/Panel/Variable model
│   ├── query_builder.py    # rawSql variable interpolation; Raw passthrough class
│   ├── runtime.py          # BQ client, run_query, variable_options,
│   │                       #   plotly_combined_figure, make_bulk_sql,
│   │                       #   make_combined_sql, rewrite_histogram_call
│   ├── notebook_builder.py # Assemble .ipynb; serialize/filter variables;
│   │                       #   _compact_json; _filter_for_cached
│   └── widget_builder.py   # Controls class: ipywidgets + chained query dropdowns
├── tools/
│   ├── convert.py          # Driver: parse dashboard → write notebooks.stage/
│   └── gen_docs.py         # Fetch BQ routine/table definitions → docs/functions/*.md
├── dashboards/             # Input: Grafana dashboard JSON files
├── docs/
│   └── functions/          # Generated BQ reference docs (one .md per routine/table)
├── notebooks.stage/        # Converter output — gitignored, rerun-safe
└── notebooks/              # Curated .ipynb files (hand-merged from notebooks.stage/)
```

### Notebook output convention

The converter **always writes to `notebooks.stage/`**, never to `notebooks/`.
`notebooks.stage/` is gitignored and safe to overwrite on every run. The user
manually merges staged output into `notebooks/`. Never write converter output
directly into `notebooks/`.

## Workflow

```bash
# 1. Drop Grafana dashboard JSON into dashboards/

# 2. Document the BQ resources it references (for review before converting)
python tools/gen_docs.py "dashboards/<dashboard>.json"
#    → writes docs/functions/*.md; review the derived Description sections

# 3. Generate the notebook
python tools/convert.py "dashboards/<dashboard>.json"
#    → writes notebooks.stage/<slug>.ipynb

# 4. Merge into notebooks/ by hand

# 5a. Interactive mode
jupyter notebook notebooks/<slug>.ipynb

# 5b. Webapp mode (disable token auth for local use)
voila --ServerApp.token='' --ServerApp.password='' notebooks/<slug>.ipynb
```

## URL Parameter Presets (webapp mode)

Voilà injects the HTTP request's query string into `os.environ["QUERY_STRING"]`
before executing the notebook. The generated URL-params cell reads it via:

```python
from voila.utils import get_query_string
import urllib.parse
qs = get_query_string() or ""          # blocks in preheat mode; instant otherwise
url_params = {k: vs[0] if len(vs)==1 else vs
              for k, vs in urllib.parse.parse_qs(qs).items()}
```

Example URL (single-value params only; multi-select can't be set this way):
```
http://localhost:8866/voila/render/regional_details_dashboard.ipynb?anchor=lga&radius=100&ISPcount=10&binSize=25
```

For scripted or test overrides set `DASH_PRESETS` to a JSON object:
```bash
DASH_PRESETS='{"anchor":"lga","region":["lga04","lga05"]}' voila notebooks/<slug>.ipynb
```

## Grafana JSON Structure (Key Fields)

- **`templating.list`** — template variables (dropdowns); each has `name`, `type`,
  `options`, `current`. Query-type variables carry `query.rawSql` (BigQuery SQL that
  populates the dropdown options).
- **`panels`** — `type`, `title`, `targets`, `fieldConfig`. Types seen: `text`
  (markdown), `table`, `ae3e-plotly-panel` (Plotly figure whose trace shaping was a
  client-side JS script — reimplemented in `runtime.plotly_combined_figure`), `row`
  (section header, may carry a `repeat` variable name).
- **`targets`** — each carries `rawSql` with `$var` / `${var}` Grafana interpolation
  placeholders and format modifiers: `${var:regex}`, `${var:raw}`,
  `${__from:date:iso}`, `${__to:date:iso}`.

## Key Architecture Decisions

### Variable interpolation (`converter/query_builder.py`)
- `interpolate(sql, values, from_dt, to_dt)` substitutes all `${var}` / `$var`
  references. Default `missing="keep"` silently preserves placeholders in SQL
  comments.
- `${var:regex}` — Grafana-exact escaping (spaces not escaped; only regex
  metacharacters). Multi-value list → `(a|b|c)`.
- `Raw(value)` — wrap a pre-built string to bypass all format escaping (used for
  the bulk-ISP ASN regex).

### Chained query dropdowns (`converter/widget_builder.py`)
- `Controls(variables, client, presets)` builds ipywidgets from serialized
  variable dicts and wires dependency observers by inspecting each variable's
  `query_sql` for references to other variable names.
- `default_select: "all" | "half"` — set in serialized VARIABLES to override the
  Grafana default selection. Applied on initial load and re-applied when the parent
  variable changes and none of the previous values are still valid (e.g. switching
  anchor metro).

### Bulk ISP queries (`converter/runtime.py`)
Histogram repeat panels (one plot row per client ISP) previously fired one BQ
query per ISP. Now a single query fetches all ISPs:

1. **`asn_regex(isp_names)`** — builds `"18881 |28573 |..."` matching on the
   leading ASN digits + space delimiter (stable across ISP name text changes).
2. **`rewrite_histogram_call(sql, method)`** — replaces the `access_ndt7_isp_histograms`
   wrapper call with a direct call to the correct underlying function:
   - `cached` → `access_ndt7_cached_histograms(field, siteRegex, ispCount)`
   - `live` → `unified_ndt7_isp_histograms(method, xAxis, binSize, field, startDate, endDate, siteRegex)`
   - `experimental` → `experimental_ndt7_isp_histograms(...)` (same 7-arg signature)
3. **`make_bulk_sql(sql)`** — adds `ISPname` to the CTE `SELECT`, `GROUP BY`,
   every `PARTITION BY siteName` window clause, and the final `SELECT`. Server
   selection (`siteRegex` / `siteName`) is unchanged.
4. **`make_combined_sql(sql)`** — replaces the `CASE "$mode" WHEN "pdf"…ELSE…END AS
   data` block with explicit `AS pdf` and `AS cdf` columns; strips the
   `OR ("$mode" = "cdf")` HAVING clause so all bins are always returned.
5. Python then filters `df[df["ISPname"].str.startswith(asn + " ")]` per ISP for
   each plot.

Render pipeline per repeat panel:
```
rewrite_histogram_call → make_bulk_sql → make_combined_sql → interpolate → run_query
```

### Combined PDF + CDF figure (`runtime.plotly_combined_figure`)
Single figure with two vertically offset Y axes:
- PDF: bottom domain `[0, 0.44]`, left axis, `rangemode="nonnegative"`.
- CDF: top domain `[0.56, 1.0]`, right axis, fixed range `[0, 1]`.
- 12% gap between domains; legend anchored inside the gap.
- One legend entry per M-Lab server site (PDF trace shows, CDF shares the
  `legendgroup` silently).

### Variable filtering for cached histograms (`notebook_builder._filter_for_cached`)
At conversion time the serialized VARIABLES are pruned to match what
`access_ndt7_cached_histograms` supports:
- `method`: `cached` only.
- `table_field`: `MinRTT`, `MeanThroughputMbps`, `LossRate`, `linearMinRTT`.
- `field` (Fourth column): `none`, `MinRTT`, `MeanThroughputMbps`, `LossRate`.
- `mode` (pdf/cdf selector): removed — CDF and PDF are always shown together.
- `region` / `ClientISP`: `default_select: "all"` / `"half"` added.

### BQ function documentation (`tools/gen_docs.py`)
Parses a dashboard JSON, finds every `\`...\`` backtick-quoted BQ resource, fetches
its definition via `bq show --routine` or `bq show`, probes the output schema
live (since TVF return types are not in the API), and writes one reviewable
Markdown file per resource to `docs/functions/`. Re-running overwrites the
generated files including any hand-edited Description prose.

## Requirements

```
jupyter
voila
ipywidgets
plotly
nbformat
google-cloud-bigquery
pandas
```

`bq` / `gcloud` (Google Cloud SDK) are used out-of-band by `tools/gen_docs.py`
and `tools/convert.py`; the notebooks themselves use `google-cloud-bigquery`.
