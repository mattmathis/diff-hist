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
├── jupyter_server_config.py  # No-auth local Voilà + kernel culling
├── converter/
│   ├── parser.py           # Parse Grafana JSON → Dashboard/Panel/Variable model
│   ├── query_builder.py    # rawSql variable interpolation; Raw passthrough class;
│   │                       #   format_regex (Grafana-exact escaping)
│   ├── runtime.py          # BQ client, run_query, variable_options;
│   │                       #   fetch_histograms (Python dispatch + densification);
│   │                       #   plotly_combined_figure (PDF+CDF dual-axis figure);
│   │                       #   metro_barchart, metro_nav_html (bar chart + links);
│   │                       #   fleet_map (Scattergeo world map);
│   │                       #   asn_regex; legacy SQL-transform helpers
│   ├── notebook_builder.py # Assemble .ipynb; serialize/filter variables;
│   │                       #   _filter_for_cached, _filter_for_exp,
│   │                       #   _filter_for_fleet, _filter_for_barchart;
│   │                       #   _compact_json; build_notebook(flavor=)
│   └── widget_builder.py   # Controls class: ipywidgets + chained query dropdowns;
│                           #   CheckboxGroup; textbox (Text widget) support;
│                           #   dynamic_default field for runtime-evaluated defaults
├── tools/
│   ├── convert.py          # Driver: parse dashboard → write notebooks.stage/
│   │                       #   auto-detects flavor; calls gen_deps.gen_all()
│   ├── gen_deps.py         # Generate docs/dependencies.md (BQ↔notebook map)
│   └── gen_docs.py         # Fetch BQ routine/table definitions → docs/functions/*.md
├── dashboards/             # Input: Grafana dashboard JSON files
├── docs/
│   ├── dependencies.md     # Auto-generated BQ↔notebook dependency map
│   └── functions/          # Generated BQ reference docs (one .md per routine/table)
├── notebooks.stage/        # Converter output — gitignored, rerun-safe
└── notebooks/              # Curated .ipynb files (hand-merged from notebooks.stage/)
```

### Notebook output convention

The converter **always writes to `notebooks.stage/`**, never to `notebooks/`.
`notebooks.stage/` is gitignored and safe to overwrite on every run. The user
manually merges staged output into `notebooks/`. Never write converter output
directly into `notebooks/`.

## Dashboards

| File | Flavor | Notes |
|------|--------|-------|
| `Regional Details Dashboard-*.json` | `prod` | Intended for public use; cached histograms only |
| `Experimental Regional Details Dashboard.json` | `exp` | Developer use; multiple BQ backends; composite method string |
| `Global Metro Bar Chart-*.json` | `barchart` | Summary bar charts per metro; navigation links to regional details |
| `Fleet and Egress load-*.json` | `fleet` | Table + global map; no histograms |

Flavor is auto-detected by `tools/convert.py`: `methodsrc` variable → `exp`;
`method` variable → `prod`; `barchart` panel type → `barchart`; else → `fleet`.
Override with `--flavor prod|exp|barchart|fleet`.

## Workflow

```bash
# 1. Drop Grafana dashboard JSON into dashboards/

# 2. Document the BQ resources it references (for review before converting)
python tools/gen_docs.py "dashboards/<dashboard>.json"
#    → writes docs/functions/*.md; review the derived Description sections

# 3. Generate the notebook (flavor auto-detected)
python tools/convert.py "dashboards/<dashboard>.json"
#    → writes notebooks.stage/<slug>.ipynb
#    → also regenerates docs/dependencies.md

# 4. Merge into notebooks/ by hand

# 5a. Interactive mode
jupyter notebook notebooks/<slug>.ipynb

# 5b. Webapp mode (no-auth config picked up automatically from jupyter_server_config.py)
voila notebooks/<slug>.ipynb

# Kill a running Voilà server
kill $(lsof -t -i:8866)   # default port 8866
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

Example URL (single-value params only; multi-select requires `DASH_PRESETS`):
```
http://localhost:8866/voila/render/regional_details_dashboard.ipynb?anchor=lga&radius=100&ISPcount=10&binSize=25
```

For scripted or test overrides set `DASH_PRESETS` to a JSON object:
```bash
DASH_PRESETS='{"anchor":"lga","region":["lga04","lga05"]}' voila notebooks/<slug>.ipynb
```

## Key Architecture Decisions

### Python histogram dispatch (`runtime.fetch_histograms`)

Replaces the BQ wrapper functions (`access_ndt7_isp_histograms`,
`access_exp_ndt7_isp_histograms`) with a single Python function that dispatches on
`method` to the right BQ backend:

| method contains | BQ function called | Args |
|---|---|---|
| `cached` | `access_ndt7_cached_histograms` | `(field, site_regex, isp_count)` |
| `exp` / `DS16` / `DS1C` / `DS1V` | `experimental_ndt7_isp_histograms` | `(method, x_axis, bin_size, field, start, end, site_regex)` |
| anything else (live) | `unified_ndt7_isp_histograms` | same 7 args |

After fetching, the function:
1. **Densifies** sparse backends (experimental/unified) per `(siteName, ISPname)` pair
   using each pair's own `[minBinIX, maxBinIX]` range (not metro-wide, to avoid
   outlier-driven zero-padding). Cached data is already dense from BQ.
2. **Computes `bin`** from `binIX` using the field-appropriate formula.
3. **Filters** by `isp_regex` (ASN-prefix regex covering all selected ISPs).
4. **Computes PDF and CDF** per `(siteName, ISPname)` on all bins (accurate CDF).
5. **Adds `n_tests`** = total hist count per pair (shown in legend labels).
6. **Applies binSize thinning** to final output if `bin_size < 50`.

Returns `(bin, pdf, cdf, siteName, ISPname, n_tests)`. BQ errors propagate
unchanged so they surface in the notebook output for debugging.

The legacy SQL-transform helpers (`make_bulk_sql`, `make_combined_sql`,
`rewrite_histogram_call`) remain in `runtime.py` but are no longer called from
generated notebooks.

### Combined PDF + CDF figure (`runtime.plotly_combined_figure`)

Single figure with two vertically offset Y axes:
- PDF: bottom domain `[0, 0.44]`, left axis.
- CDF: top domain `[0.56, 1.0]`, right axis, fixed range `[0, 1]`.
- Legend anchored inside the gap; one entry per site showing `"{site} ({n:,})"`.
- Height 600 px; 2 px line width.

### Variable interpolation (`converter/query_builder.py`)

Used for the summary-table SQL (`regional_report`) only — histogram data is now
fetched via `fetch_histograms`, not SQL templates.

- `interpolate(sql, values, from_dt, to_dt)` — default `missing="keep"`.
- `${var:regex}` — Grafana-exact escaping; multi-value → `(a|b|c)`.
- `format_regex(values)` — used standalone to build `site_regex` for `fetch_histograms`.
- `Raw(value)` — bypass all format escaping (still used for ASN regex in older paths).

### Chained query dropdowns (`converter/widget_builder.py`)

- `Controls(variables, client, presets)` — wires observer chains by inspecting
  each query variable's SQL for references to other variable names.
- `default_select: "all" | "half"` — re-applied when anchor changes invalidates
  the previous server/ISP selection.
- `CheckboxGroup` — multi-select implemented as individual Checkbox widgets.
- `type=textbox` variables → `widgets.Text`.

### Notebook flavors (`notebook_builder.build_notebook(flavor=)`)

**`prod`** — `_filter_for_cached`:
- `method` locked to `cached`.
- `table_field` / `field` pruned to cached-supported fields.
- `mode` removed (always PDF+CDF). `verbose` renamed to `table_style` (none/Summary/Verbose).
- `metrics` multi-select added (default: `MeanThroughputMbps`).
- Cached date range displayed after Run (from `metroStart`/`metroEnd` columns).
- No date pickers — data is always the latest cached window.

**`exp`** — `_filter_for_exp`:
- `methodsrc` (exp-DS16 / exp-DS1C / cached / exp-DS1V), `locate`, `clientname`
  kept from dashboard.
- `method` composite variable dropped — the render cell builds it in Python:
  `f"{methodsrc}-{locate}-{sub_method}[-{extra_flags}]"`.
- `sub_method` dropdown added (default / showIPv / showEarly / showNames / showName=…).
- `extra_flags` free-text input added (arbitrary subselector flags appended to method).
- `metrics` multi-select added. No summary table (table_style=none always).
- No option pruning — exp backend supports more fields than cached.
- DatePicker widgets for `from_dt` / `to_dt`, defaulting to the most recent
  Sunday week (UTC): days since Sunday = `(_today_utc.weekday() + 1) % 7`.

**`barchart`** — `_filter_for_barchart`:
- Drops Grafana-specific variables: `prometheus`, `detailURL`.
- Keeps `verbose` as-is (controls single-site metro inclusion).
- No metrics chooser; the barchart panels are fixed.

**`fleet`** — `_filter_for_fleet`:
- Expands `$__all` sentinel to explicit option values.
- Overrides `endDate` with `dynamic_default` = two days ago (UTC) so the
  notebook always opens on a recent date rather than the stale Grafana value.
- No metrics chooser; table-only layout.

### Bar chart navigation (`runtime.metro_barchart`, `runtime.metro_nav_html`)

`metro_barchart(df, title, isp_count, target_notebook)` — grouped Plotly bar chart
with `customdata` URL fields and hover hints. `display(fig)` + `fig._config =
{'responsive': False}` prevents plot-area reflow when a second chart is shown.

`metro_nav_html(df, isp_count, target_notebook)` — plain HTML `<div>/<ul>/<a href>`
navigation list rendered always-visible below each chart. Replaces all
JS-based click approaches (FigureWidget.on_click, display(Javascript(…)),
`<script>` tags) which fail in Voilà due to JupyterLab version mismatch or
HTML sanitisation.

### Fleet global map (`runtime.fleet_map`)

`fleet_map(df)` — Plotly `Scattergeo` world map. One marker per metro (Subtotal
by Metro rows from `global_fleet_inventory`). Marker colour encodes
log₁₀(tests/day); hover shows metro, sites, data/test volumes, and cost.
The map is rendered when `display` selector includes `metros` or `sites`.

### Code cell hiding and UI defaults

All code cells have `cell.metadata["jupyter"]["source_hidden"] = True` so they
are collapsed by default in JupyterLab and Voilà. Users see only widget output.

Selector Diagnostics is rendered in an `ipywidgets.Accordion` with
`selected_index = None` so it is collapsed by default.

### BQ dependency documentation (`tools/gen_deps.py`)

`gen_all()` scans all dashboard JSON files, detects flavor, extracts SQL-level
BQ resource references, and appends the static `fetch_histograms` dispatch table
(resources not visible in SQL). Writes `docs/dependencies.md`. Called
automatically after every `tools/convert.py` run. Update
`_FETCH_HISTOGRAMS_DISPATCH` in the file if `runtime.fetch_histograms` dispatch
logic changes.

### No-auth local Voilà (`jupyter_server_config.py`)

```python
c.Voila.token = ''
c.Voila.password = ''
c.Voila.ip = '0.0.0.0'
c.Voila.open_browser = False
c.MappingKernelManager.cull_idle_timeout = 120
c.MappingKernelManager.cull_interval = 30
c.MappingKernelManager.cull_connected = True
```

Picked up automatically by `voila` (and `jupyter`) when run from the project
root. Disables auth for local use; culls idle kernels after 2 minutes.

### BQ function documentation (`tools/gen_docs.py`)

Parses a dashboard JSON, fetches each backtick-quoted BQ resource via
`bq show --routine` or `bq show`, probes the live output schema, and writes one
reviewable Markdown file per resource to `docs/functions/`. Re-running overwrites
files including hand-edited Description prose.

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
