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

> **BQ backend source (`backend/`):** the deployable SQL for that fleet
> (routines/views/tables + the weekly cache-builder scheduled query) is extracted
> verbatim into `backend/` so **git is the source of truth**, following the
> `bq-tools` skill method. `tools/extract_backend.py` pulls the authoritative
> `CREATE …` text from `INFORMATION_SCHEMA.…ddl` and walks the dependency closure
> from the notebook-facing routines plus the tracked scheduled queries (29
> objects + 1 scheduled query). See `backend/README.md`. (`bq` must be
> authenticated where the tool runs; the annealing SA is the reliable path since
> the local user token lapses under the Workspace reauth policy.)

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
├── voila.json                # No-auth + kernel culling; auto-loaded from repo root
├── converter/
│   ├── parser.py           # Parse Grafana JSON → Dashboard/Panel/Variable model
│   ├── query_builder.py    # rawSql variable interpolation; Raw passthrough class;
│   │                       #   format_regex (Grafana-exact escaping)
│   ├── runtime.py          # BQ client, run_query, variable_options;
│   │                       #   fetch_histograms (Python dispatch + densification);
│   │                       #   plotly_combined_figure (PDF+CDF dual-axis figure);
│   │                       #   metro_barchart, metro_barchart_clickable (bar chart + click nav);
│   │                       #   fleet_map (Scattergeo world map);
│   │                       #   run_competition_report, run_calibration_report,
│   │                       #   plotly_calibration_scatter (internal/calibration reports);
│   │                       #   regional_details_url, isp_link_text, breadcrumb_to_url
│   │                       #     (Regional Details deep-link builders);
│   │                       #   asn_regex; legacy SQL-transform helpers
│   ├── notebook_builder.py # Assemble .ipynb; serialize/filter variables;
│   │                       #   _filter_for_cached, _filter_for_exp,
│   │                       #   _filter_for_fleet, _filter_for_barchart,
│   │                       #   _filter_for_calibration, _filter_for_internal;
│   │                       #   _compact_json; build_notebook(flavor=)
│   └── widget_builder.py   # Controls class: ipywidgets + chained query dropdowns;
│                           #   CheckboxGroup; textbox (Text widget) support;
│                           #   dynamic_default field for runtime-evaluated defaults;
│                           #   presets + asn_presets (URL-param pre-selection)
├── tools/
│   ├── convert.py          # Driver: parse dashboard → write notebooks.stage/
│   │                       #   auto-detects flavor; calls gen_deps.gen_all()
│   ├── gen_deps.py         # Generate docs/dependencies.md (BQ↔notebook map)
│   ├── gen_docs.py         # Fetch BQ routine/table definitions → docs/functions/*.md
│   └── extract_backend.py  # Extract the BQ fleet (closure from notebook entry
│                           #   points + scheduled queries) → backend/*.sql via
│                           #   INFORMATION_SCHEMA.ddl; round-trip deterministic
├── backend/                # Deployable SQL source of the BQ fleet (git = truth)
│   ├── routines/           # TABLE FUNCTIONs (histogram + report logic)
│   ├── views/ · tables/    # views; table schema DDL (data lives in BQ)
│   ├── scheduled/          # transfer-config SQL (weekly cache builder) + schedule
│   └── MANIFEST.json       # types, dependency edges, deploy order, scheduled qs
├── dashboards/             # Input: Grafana dashboard JSON files
│   └── internal/           # Internal-only dashboards (competition reports)
├── docs/
│   ├── dependencies.md     # Auto-generated BQ↔notebook dependency map
│   └── functions/          # Generated BQ reference docs (one .md per routine/table)
├── notebooks.stage/        # Converter output — gitignored, rerun-safe
│   └── internal/           # Staged output for internal-flavor dashboards
└── notebooks/              # Curated .ipynb files (hand-merged from notebooks.stage/)
    └── internal/           # Curated internal-only notebooks
```

### Notebook output convention

The converter **always writes to `notebooks.stage/`**, never to `notebooks/`.
`notebooks.stage/` is gitignored and safe to overwrite on every run. The user
manually merges staged output into `notebooks/`. Never write converter output
directly into `notebooks/`.

### Keeping outputs out of git

Two complementary mechanisms prevent notebook outputs from being committed:

- **`nbstripout`** (git filter) — strips outputs at `git add` time. The working
  copy keeps outputs for your interactive session; they are never staged.
  Install once per clone: `pip install nbstripout && nbstripout --install`
- **pre-commit hook** (`.git/hooks/pre-commit`) — clears outputs from any staged
  notebooks as a safety net before each commit.

## Dashboards

| File | Flavor | Notes |
|------|--------|-------|
| `Regional Details Dashboard-*.json` | `prod` | Intended for public use; cached histograms only |
| `Experimental Regional Details Dashboard.json` | `exp` | Developer use; multiple BQ backends; composite method string |
| `Global Metro Bar Chart-*.json` | `barchart` | Summary bar charts per metro; navigation links to regional details |
| `Fleet and Egress load-*.json` | `fleet` | Table + global map; no histograms |
| `M-Lab Calibration Dashboard-*.json` | `calibration` | KS-distance scatter + ranked report table; Details column deep-links to Regional Details |
| `internal/Differential Competition Report for *-*.json` | `internal` | Internal-only competition reports (minRTT / throughput); Details column deep-links to Regional Details. Staged to `notebooks.stage/internal/` |

Flavor is auto-detected by `tools/convert.py` in this order: panel SQL contains
`competition_report` → `internal`; SQL contains `calibration_report` →
`calibration`; `methodsrc` variable → `exp`; `method` variable → `prod`;
`barchart` panel type → `barchart`; else → `fleet`.
Override with `--flavor prod|exp|barchart|fleet|calibration|internal`.

`internal`-flavor dashboards are written to the `internal/` subdirectory of the
output dir, and the converter strips the `_do_not_share` suffix from the slug.

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

# 5b. Webapp mode (run from repo root so voila.json is picked up automatically)
voila notebooks/<slug>.ipynb

# Kill a running Voilà server
kill $(lsof -t -i:8866)   # default port 8866
```

## GCE Deployment

| Instance | IP | Domain | Purpose |
|---|---|---|---|
| `annealing` | 34.58.12.41 (static) | annealing.mattmathis.net | Primary public Voilà server |
| `mm-byos-tester3` | 34.68.11.17 (ephemeral) | — | Reserved for limited testing only |

Voilà runs as a systemd service on both instances (starts on boot, restarts on crash).
`annealing` has a static IP and nginx reverse proxy with TLS; `mm-byos-tester3` IP may change on stop/start.

### BigQuery data access (annealing identity)

The notebooks query BQ as the **instance's default compute service account**
(`1031725934094-compute@developer.gserviceaccount.com`) — the GCE application
default credential, no user `gcloud` login or ADC file involved. That SA reads
`mlab-collaboration.mm_preproduction`, so **`method=cached` works**.

**`method=live` / `live-DS16` reach further:** the `unified_ndt7_isp_histograms`
function calls `get_ndt7_data`, which reads M-Lab source tables in the
**`measurement-lab`** project (e.g. `measurement-lab.ndt.ndt7_dynamic`). The SA
must be authorized for that data or the query returns
`403 Access Denied: … measurement-lab:ndt.ndt7_dynamic`.

**Access model:** the SA gets M-Lab data access the same way people do — by being
a **member of `discuss@measurementlab.net`** (M-Lab grants BQ read to that group),
so it inherits every grant the group has, present and future. The SA has been
added to the group, so `live` / `live-DS16` work on annealing. To re-grant on a
new instance, add its SA to the group (Google Groups UI, or `gcloud identity
groups memberships add --group-email=discuss@measurementlab.net
--member-email=<sa-email>`); allow a few minutes to propagate, and note the group
must permit external members (SA domain is `developer.gserviceaccount.com`).

> **Note:** Regional Details is no-auth, so the SA's discuss-level access means
> the public server can run any query the discuss group can — the intended
> "publicly queryable" behaviour.

### nginx + TLS setup (one-time on annealing)

nginx proxies HTTPS traffic to Voilà on localhost:8866. TLS cert is managed by certbot (auto-renews via systemd timer).

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/annealing << 'EOF'
server {
    listen 80;
    server_name annealing.mattmathis.net;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location = / {
        return 302 /voila/render/index.ipynb;
    }

    location / {
        proxy_pass http://127.0.0.1:8866;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/annealing /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d annealing.mattmathis.net
```

certbot edits the nginx config to add the HTTPS server block and schedules auto-renewal.
After certbot runs, add the `location = /` redirect to `sites-enabled/annealing` manually
(certbot rewrites `sites-available` so the pre-certbot tee may not survive).

### Voilà systemd setup (one-time on the instance)

```bash
sudo tee /etc/systemd/system/voila.service << 'EOF'
[Unit]
Description=Voila notebook server
After=network.target

[Service]
User=mattmathis
WorkingDirectory=/home/mattmathis/Projects/diff-hist
ExecStart=/home/mattmathis/Projects/diff-hist/.venv/bin/voila notebooks/
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable voila
sudo systemctl start voila
```

### Normal operations

```bash
sudo systemctl status voila    # check status
sudo systemctl restart voila   # restart after pulling new notebooks
sudo journalctl -u voila -f    # tail logs
```

### Debugging (manual start)

```bash
sudo systemctl stop voila                  # hand control to yourself
voila notebooks/ --debug                   # run manually (Ctrl+C to stop)
sudo systemctl start voila                 # hand back to systemd when done
```

### Deploying notebook updates

```bash
# On the instance:
git pull
sudo systemctl restart voila
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

**Regional Details deep-link params** (used by the Details/breadcrumb links and
the bar-chart navigation):
- `sites=lga04,lga05` — pre-select servers by site code. If `anchor=` is absent,
  it is derived from the first site code.
- `ISPs=7922,8030` — pre-select client ISPs by **AS number** (matched against the
  first space-delimited token of each ISP option). Wired through
  `Controls(..., asn_presets=...)` and re-applied after each chained-query refresh.

`sites=`/`ISPs=` change the pre-*selection* only, not the dropdown *contents* —
the selected entries are assumed to be present in the populated options.

For scripted or test overrides set `DASH_PRESETS` to a JSON object:
```bash
DASH_PRESETS='{"anchor":"lga","region":["lga04","lga05"]}' voila notebooks/<slug>.ipynb
```

## Key Architecture Decisions

### Python histogram dispatch (`runtime.fetch_histograms`)

Replaces the BQ wrapper functions (`access_ndt7_isp_histograms`,
`access_exp_ndt7_isp_histograms`) with a single Python function that dispatches
on the **backend token** — the first `-`-delimited segment of `method` — to the
right BQ backend. Trailing modifiers (`DS16`, `DS1C`, `DS1V`, `showLocate`, …)
are passed through in the full `method` string but do **not** select the
backend, so e.g. `live-DS16` runs against the live (unified) backend:

| backend token | BQ function called | Args |
|---|---|---|
| `cached` | `access_ndt7_cached_histograms` | `(field, site_regex, isp_count)` |
| `exp` | `experimental_ndt7_isp_histograms` | `(method, x_axis, bin_size, field, start, end, site_regex)` |
| anything else (`live`) | `unified_ndt7_isp_histograms` | same 7 args |

Raw query results are memoised by SQL text in the module-level
`runtime._HIST_CACHE` (override per-call with the `cache=` arg; clear with
`runtime.clear_hist_cache()`). Because the metric/`field` is part of the SQL,
each metric caches separately, and the cache persists across re-renders so
toggling the ISP selection or re-clicking **Run** reuses the query rather than
re-hitting BQ. The cache is per-kernel (see the Voilà vs. Jupyter section).

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
- `sites=` (the full selected-server set) forces **every** selected server into
  the legend even with zero data for that ISP — missing servers get an empty
  trace labelled `"(0)"`. Colours index into the sorted union of `sites`, which
  is identical for every per-ISP chart, so a given server keeps the same colour
  across all charts. `sites=None` falls back to data-only (legacy behaviour).

### Variable interpolation (`converter/query_builder.py`)

Used for the summary-table SQL (`regional_report`) only — histogram data is now
fetched via `fetch_histograms`, not SQL templates.

- `interpolate(sql, values, from_dt, to_dt)` — default `missing="keep"`.
- `${var:regex}` — Grafana-exact escaping; multi-value → `(a|b|c)`.
- `format_regex(values)` — used standalone to build `site_regex` for `fetch_histograms`.
- `Raw(value)` — bypass all format escaping (still used for ASN regex in older paths).

### Chained query dropdowns (`converter/widget_builder.py`)

- `Controls(variables, client, *, presets=None, asn_presets=None, after=None)` —
  wires observer chains by inspecting each query variable's SQL for references
  to other variable names.
- `presets` — per-variable value overrides (from URL params).
- `asn_presets` — maps a variable name to a list of AS-number strings; after that
  variable's options are (re)populated, any option whose first space-delimited
  token matches is selected. Takes priority over `default_select` and is
  re-applied on every chained refresh (e.g. `{'ClientISP': ['7922', '8030']}`).
- `after` — maps a variable name to an extra widget spliced into the layout
  immediately after that variable's row (used to place the date row right after
  the method selector). Ignored if the named variable has no widget.
- `default_select: "all" | "half"` — re-applied when anchor changes invalidates
  the previous server/ISP selection.
- `CheckboxGroup` — multi-select implemented as individual Checkbox widgets.
- `type=textbox` variables → `widgets.Text`.

**Dynamic widget visibility** (wired in the `_CONTROLS` render cell, not in
`Controls`): observers toggle `layout.display` — pure widget state, so Voilà-safe.
- **Date row** — hidden when the backend token (`method`/`methodsrc` split on `-`)
  is `cached`; shown for `live`/`exp`.
- **`extra_rows`** — shown only when the selected servers (`region`) span more
  than one metro (distinct 3-letter IATA prefixes of the site codes).

### Tables: sticky headers (`runtime.to_html_sticky`)

`to_html_sticky(df, **kwargs)` is a drop-in for `df.to_html(...)` that injects
inline `position: sticky; top: 0` styling into each `<th>`, so column labels stay
pinned while the body scrolls inside its `overflow:auto` container. Uses **inline
`style`** (not a `<style>` block or `Styler.set_sticky`) because Voilà's DOMPurify
strips `<style>`; the background uses `var(--jp-layout-color0,#fff)` to adapt to
light/dark themes. Used by the fleet, competition-report, and calibration tables.

### Notebook flavors (`notebook_builder.build_notebook(flavor=)`)

**`prod`** — `_filter_for_cached`:
- `method` offers `cached` / `live` / `live-DS16` (default `cached`).
- `table_field` / `field` pruned to cached-supported fields.
- `mode` removed (always PDF+CDF). `verbose` renamed to `table_style` (none/Summary/Verbose).
- `metrics` multi-select added (default: `MeanThroughputMbps`).
- `ClientISP` defaults to **all** selected (`default_select='all'`).
- `extra_rows` textbox inserted after `ISPcount` (Client Rows); see Extra rows below.
- Cached date range displayed after Run (from `metroStart`/`metroEnd` columns).
- Uses `_DATE_PICKERS_DURATION` (end date + duration 1/7/28/30), hidden unless
  the method is non-cached (see Dynamic widget visibility).

**`exp`** — `_filter_for_exp`:
- `methodsrc` (exp-DS16 / exp-DS1C / cached / live / exp-DS1V), `locate`, `clientname`
  kept from dashboard.
- `method` composite variable dropped — the render cell (`_EXP_METHOD_PREAMBLE`)
  builds it in Python: `cached`→`"cached"`, `live`→`"live"` (exp-only modifiers
  are **not** applied, so switching exp→live can't pollute the live method string
  with hidden values), exp backends → `exp-<locate>-<sub_method>[-<extra_flags>]`.
- `sub_method` dropdown and `extra_flags` textbox inserted at the top, before anchor.
- `verbose` renamed to `table_style` (none/Summary/Verbose, default none) — same as prod.
- `metrics` multi-select added. No option pruning — exp backend supports more fields.
- `ClientISP` defaults to **all** selected; `extra_rows` textbox after `ISPcount`.
- Keeps its Start/End `DatePicker` range (`_DATE_PICKERS_EXP`), defaulting to the
  most recent Sunday week (UTC); hidden unless the method is non-cached.

**Extra rows** (`prod` / `exp`) — a textbox (default `0`) shown only when the
selected servers span multiple metros. Its value is **added to `ISPcount` only
for the BQ histogram fetch** (so a multi-metro query returns enough ranked ISPs
to cover every metro); it is not used for the ClientISP dropdown or display.

**`barchart`** — `_filter_for_barchart`:
- Drops Grafana-specific variables: `prometheus`, `detailURL`.
- Keeps `verbose` as-is (controls single-site metro inclusion).
- No metrics chooser; the barchart panels are fixed.

**`fleet`** — `_filter_for_fleet`:
- Expands `$__all` sentinel to explicit option values.
- Overrides `endDate` with `dynamic_default` = two days ago (UTC) so the
  notebook always opens on a recent date rather than the stale Grafana value.
- No metrics chooser; table-only layout.

**`calibration`** — `_filter_for_calibration`:
- Drops infrastructure variables (`datasource`, `dataset`, `detailURL`) and
  parameters locked as Python constants (`xAxis`, `binSize`).
- `${dataset}` is pre-substituted in any remaining `query_sql` since the
  `dataset` variable is dropped.
- `method` limited to `cached` / `live` (default `cached`).
- `region` is dropped in favour of the **same stubbed `organization` selector as
  the competition reports** (`_org_var()` / `_ORG_QUERY`, default `.*` = all orgs).
  Its value is wired into the report's `region_regex` slot as a placeholder until
  the org-filter backend work lands. `radius` defaults to 100.
- Renders a KS-distance-vs-ratio scatter plus the ranked calibration report
  table (`_CALIBRATION_RENDER`, sticky headers). Uses `_DATE_PICKERS_DURATION`.

**`internal`** — `_filter_for_internal`:
- Drops `datasource`, `PromSource`, `autoOrg`, `dataset`, `detailURL`, `dateRange`.
- Replaces `organization` with a BQ-backed query dropdown (`_ORG_QUERY`,
  default `.*` = all orgs); `method` limited to `cached` / `live`.
- Render cell (`_INTERNAL_RENDER`) detects report type (`throughput` vs `minRTT`)
  from panel SQL. Uses `_DATE_PICKERS_DURATION`.
- Written to the `internal/` output subdirectory (see Dashboards above).

### Bar chart navigation (`runtime.metro_barchart`, `runtime.metro_barchart_clickable`)

`metro_barchart(df, title, isp_count, target_notebook)` — grouped Plotly bar chart
with `customdata` URL fields. `display(fig)` + `fig._config =
{'responsive': False}` prevents plot-area reflow when a second chart is shown.

`metro_barchart_clickable(df, isp_count, target_notebook, link_widget)` — wraps the
figure in a `FigureWidget`; clicking a bar updates *link_widget* (an
`ipywidgets.HTML`) with a navigation link to Regional Details. Works in Voilà
because it uses a Python `on_click` callback to update a widget — no JS injection.

### Details deep-links from report tables (`runtime.breadcrumb_to_url`)

The `internal` competition reports and the `calibration` report return a
space-delimited `breadcrumb` column (`site1 site2 ASnumber [ISPname]`). The
render templates (`_INTERNAL_RENDER`, `_CALIBRATION_RENDER`) build a clickable
**Details** link from each row by parsing that column with
`runtime.breadcrumb_to_url`, which delegates to `regional_details_url`:

- Only the **AS number** goes into the href (names are fragile); the full
  breadcrumb string remains the anchor text.
- The column is matched **case-insensitively** and the displayed header is
  renamed to `Breadcrumb`.
- The link is rendered via **`ipywidgets.HTML`** (not `IPython.display.HTML`):
  Voilà's DOMPurify strips `href` attributes from `display(HTML(...))` output
  but preserves them in widget HTML.
- The old `BCargs` column was incomplete test code and is no longer used.

> **Status:** deployed to annealing but **not yet verified against live BQ**
> (auth lapsed during development). Confirm the links resolve on a live run.

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

### Voilà vs. Jupyter behavioral differences

These notebooks run in two environments — interactive JupyterLab and the Voilà
webapp — and several design choices exist to paper over the gaps. When editing,
assume Voilà is the stricter target:

**Rendering / HTML / JS**
- **DOMPurify sanitization** — Voilà scrubs `display(HTML(...))` output: it
  strips `href`, `target`, `on*` handlers, `<script>`, `<iframe>`, and some
  inline styles. Jupyter renders it verbatim. Emit clickable/link HTML through
  **`ipywidgets.HTML`**, which is not sanitized the same way (see the Details
  deep-links and bar-chart navigation sections).
- **No injected JS runs** — `IPython.display.Javascript(...)`, `window.open`,
  and Plotly client-side JS callbacks do nothing in Voilà. Use Python
  `FigureWidget.on_click` callbacks that update a widget, never JS handlers.
- **Plotly** — `go.FigureWidget` (comm-backed) works in both; a plain
  `go.Figure` relying on native JS interactions can differ. Prefer FigureWidget
  for anything interactive.

**Execution model & errors**
- **Auto run, top-to-bottom, once** at page load — no manual or out-of-order
  cell execution. Don't rely on run order that only holds in interactive use.
- **Tracebacks suppressed by default** (`show_tracebacks=False`) — an exception
  can blank or truncate the page instead of showing an inline traceback. The
  render callbacks wrap queries in try/except and display errors as HTML, but an
  error in the setup/import cells (before that guard) can blank the page. Set
  `show_tracebacks=True` in `voila.json` while debugging.
- **No cell toolbar / edit / restart** — all interaction must go through
  widgets (the `w_run.on_click` "Run" button pattern).

**Kernel & in-memory state lifecycle**
- **Fresh kernel per page load** — reloading the Voilà URL re-runs everything
  and resets all in-memory state. In Jupyter the kernel persists across reloads.
  Consequence: `runtime._HIST_CACHE` (the histogram query cache) is per-kernel —
  it lives for one Voilà session, starts empty on every page load, and is **not**
  shared between concurrent viewers (each gets their own kernel).
- **Idle culling** — per `voila.json`, idle kernels are culled; after that the
  widgets go dead and the user must reload (losing cache/state). Jupyter doesn't
  cull like this.

**Input & environment**
- **`get_query_string()`** returns empty in plain Jupyter (no HTTP request); the
  URL-params cell handles the absent case.
- **No stdin** — `input()` and interactive prompts hang/fail in Voilà.
- **Paths & static assets** — Voilà serves under `/voila/render/...` with its own
  static handling; relative file links and served files resolve differently than
  Jupyter's `/tree`/`/notebooks` paths. Notebook-to-notebook URL param passing is
  one instance of this broader difference.

### BQ dependency documentation (`tools/gen_deps.py`)

`gen_all()` scans all dashboard JSON files, detects flavor, extracts SQL-level
BQ resource references, and appends the static `fetch_histograms` dispatch table
(resources not visible in SQL). Writes `docs/dependencies.md`. Called
automatically after every `tools/convert.py` run. Update
`_FETCH_HISTOGRAMS_DISPATCH` in the file if `runtime.fetch_histograms` dispatch
logic changes.

### No-auth local Voilà (`voila.json`)

```json
{
  "Voila": {
    "ip": "0.0.0.0",
    "token": "",
    "open_browser": false
  },
  "MappingKernelManager": {
    "cull_idle_timeout": 120,
    "cull_interval": 30,
    "cull_connected": true
  }
}
```

Picked up automatically when Voilà is run from the repo root. Disables auth,
binds to all interfaces. Kernels with an active browser connection are never
culled; abandoned kernels (browser closed) are culled after 10 minutes.

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
