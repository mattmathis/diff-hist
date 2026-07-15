"""Runtime helpers used by the generated notebooks.

The generated notebooks keep each panel's SQL and layout inline (so they read as
a faithful conversion of the dashboard) and call these helpers for the
mechanical parts: running BigQuery, turning a variable query into dropdown
options, and rebuilding an ``ae3e-plotly-panel`` figure from a result frame.

Importing this module from a notebook requires the repo root on ``sys.path``;
the generated setup cell handles that.
"""

from __future__ import annotations

import copy
import html
import io
import math
import re
import time

import pandas as pd
import plotly.graph_objects as go

try:  # only needed at notebook runtime
    from google.cloud import bigquery
except Exception:  # pragma: no cover - allows importing for unit tests
    bigquery = None


# Default billing/job project. The dashboards' panel ``project`` fields vary
# (mlab-oti, measurement-lab, ...), but the referenced dataset lives in
# mlab-collaboration and queries run fine billed to the authenticated default
# project, so we use one client unless overridden.
DEFAULT_PROJECT = "mlab-collaboration"


def bq_client(project: str | None = None):
    """Return a BigQuery client (cached on the module)."""
    if bigquery is None:
        raise RuntimeError("google-cloud-bigquery is not installed")
    return bigquery.Client(project=project or DEFAULT_PROJECT)


# Debugging surface: every run_query() execution is recorded here (SQL, elapsed
# time, row count, and DataFrame.info()) and surfaced in the notebook's collapsed
# Diagnostics panel via query_log_html().  Cache *hits* (e.g. re-used histogram
# queries, which never reach run_query) are not re-logged.  Per-kernel; capped.
_QUERY_LOG: list[dict] = []
_QUERY_LOG_MAX = 50


def clear_query_log() -> None:
    """Drop all recorded query-log entries."""
    _QUERY_LOG.clear()


def query_log() -> list[dict]:
    """Return the recorded query log (oldest first): sql, elapsed_s, n_rows, info."""
    return list(_QUERY_LOG)


def run_query(client, sql: str) -> pd.DataFrame:
    """Execute ``sql`` and return the result as a DataFrame.

    Each execution is timed and recorded in :data:`_QUERY_LOG` for the
    Diagnostics panel.  Recording never affects the returned result.
    """
    t0 = time.perf_counter()
    df = client.query(sql).result().to_dataframe(create_bqstorage_client=False)
    elapsed = time.perf_counter() - t0
    buf = io.StringIO()
    try:
        df.info(buf=buf)
        info = buf.getvalue()
    except Exception as exc:  # never let logging break a query
        info = f"(info unavailable: {exc})"
    _QUERY_LOG.append({"sql": sql, "elapsed_s": elapsed,
                       "n_rows": len(df), "info": info})
    del _QUERY_LOG[:-_QUERY_LOG_MAX]  # keep only the most recent N
    return df


def query_log_html() -> str:
    """Render :data:`_QUERY_LOG` as copyable HTML (newest first) for Diagnostics.

    Emitted through ``ipywidgets.HTML`` (not sanitized), so ``<details>``/
    ``<pre>`` and inline styles survive Voilà's DOMPurify.
    """
    if not _QUERY_LOG:
        return "<i>No queries recorded yet.</i>"
    n = len(_QUERY_LOG)
    total = sum(e["elapsed_s"] for e in _QUERY_LOG)
    parts = [f'<b>Query log</b> &mdash; {n} quer{"y" if n == 1 else "ies"}, '
             f'{total:.2f}s total (newest first):']
    for i, e in enumerate(reversed(_QUERY_LOG)):
        sql = html.escape((e["sql"] or "").strip())
        info = html.escape(e["info"] or "")
        parts.append(
            f'<details{" open" if i == 0 else ""} style="margin:4px 0">'
            f'<summary style="cursor:pointer;font-family:monospace;font-size:12px">'
            f'#{n - i} &middot; {e["elapsed_s"]:.2f}s &middot; '
            f'{e["n_rows"]:,} rows</summary>'
            f'<pre style="white-space:pre-wrap;font-size:11px;padding:6px;'
            f'background:var(--jp-layout-color1,#f5f5f5);overflow:auto">{sql}</pre>'
            f'<pre style="font-size:11px;padding:6px;overflow:auto">{info}</pre>'
            f'</details>')
    return "\n".join(parts)


def variable_options(client, sql: str) -> list[tuple[str, str]]:
    """Run a Grafana template-variable query and return ``(label, value)`` pairs.

    Grafana's convention: columns named ``__text``/``__value`` (or ``text``/
    ``value``) define the label and value; a single column is used for both.
    """
    df = run_query(client, sql)
    if df.empty:
        return []
    cols = {c.lower(): c for c in df.columns}
    text_col = cols.get("__text") or cols.get("text")
    value_col = cols.get("__value") or cols.get("value")
    if text_col and value_col:
        pairs = list(zip(df[text_col].astype(str), df[value_col].astype(str)))
    elif text_col:
        s = df[text_col].astype(str)
        pairs = list(zip(s, s))
    elif value_col:
        s = df[value_col].astype(str)
        pairs = list(zip(s, s))
    else:  # fall back to the first column for both
        s = df.iloc[:, 0].astype(str)
        pairs = list(zip(s, s))
    # De-duplicate, preserving order.
    seen, out = set(), []
    for label, value in pairs:
        if value in seen:
            continue
        seen.add(value)
        out.append((label, value))
    return out


def _split_top_args(text: str) -> list[str]:
    """Split a function's argument text on top-level commas.

    Handles nested parentheses and single/double-quoted strings so that
    ``DATE(REGEXP_EXTRACT("...", '...'))`` is kept intact as one argument.
    """
    args, depth, buf, i = [], 0, [], 0
    in_str, str_ch = False, None
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == '\\':
                i += 1
                if i < len(text):
                    buf.append(text[i])
            elif ch == str_ch:
                in_str = False
        elif ch in ('"', "'"):
            in_str, str_ch = True, ch
            buf.append(ch)
        elif ch in '([':
            depth += 1
            buf.append(ch)
        elif ch in ')]':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        args.append(''.join(buf).strip())
    return args


# Matches the opening of an access_ndt7_isp_histograms call in a SQL template.
_ISP_HIST_CALL_RE = re.compile(
    r'(\s*FROM\s+)`\$\{dataset\}\.access_ndt7_isp_histograms`\s*\('
)


def rewrite_histogram_call(sql: str, method: str) -> str:
    """Replace ``access_ndt7_isp_histograms`` with the appropriate direct call.

    The wrapper function is replaced with a direct call to the right underlying
    function based on ``method``:

    * ``cached``      → ``access_ndt7_cached_histograms(field, siteRegex, ispCount)``
    * ``live``/unified → ``unified_ndt7_isp_histograms(method, xAxis, binSize, field,
                          startDate, endDate, siteRegex)``
    * ``experimental`` → ``experimental_ndt7_isp_histograms(...)`` (same 7-arg signature)

    Called on the raw SQL template (before variable interpolation) so that
    Grafana ``${...}`` placeholders are still present and the right ones are
    preserved or dropped per function signature.

    ``ispCount`` is dropped for the live/experimental paths because those
    functions don't accept it — ISP filtering happens via the WHERE clause.
    """
    m = _ISP_HIST_CALL_RE.search(sql)
    if not m:
        return sql  # no wrapper call found (e.g. summary panels), leave unchanged

    # Balance-paren scan to find the matching close paren.
    open_pos = m.end() - 1
    depth = 0
    close_pos = open_pos
    for i in range(open_pos, len(sql)):
        ch = sql[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                close_pos = i
                break

    args = _split_top_args(sql[open_pos + 1:close_pos])
    # Argument positions (8 total in access_ndt7_isp_histograms):
    #   0: method   1: xAxis   2: binSize   3: field
    #   4: startDate  5: endDate  6: siteRegex  7: ispCount
    field      = args[3].strip()
    site_regex = args[6].strip()
    isp_count  = args[7].strip()
    from_kw    = m.group(1)          # e.g. '\n  FROM '

    if 'cached' in method.lower():
        new_call = (
            f'{from_kw}`${{dataset}}.access_ndt7_cached_histograms` ({field},\n'
            f'    {site_regex}, {isp_count})'
        )
    elif 'exp' in method.lower():
        new_call = (
            f'{from_kw}`${{dataset}}.experimental_ndt7_isp_histograms` '
            f'({args[0]}, {args[1]}, {args[2]}, {field},\n'
            f'    {args[4]},\n'
            f'    {args[5]},\n'
            f'    {site_regex})'
        )
    else:  # live / unified
        new_call = (
            f'{from_kw}`${{dataset}}.unified_ndt7_isp_histograms` '
            f'({args[0]}, {args[1]}, {args[2]}, {field},\n'
            f'    {args[4]},\n'
            f'    {args[5]},\n'
            f'    {site_regex})'
        )

    return sql[:m.start()] + new_call + sql[close_pos + 1:]


def get_cached_date_range(
    client,
    dataset: str = "mlab-collaboration.mm_preproduction",
) -> str:
    """Return the date range string for the current cached histogram data.

    Queries ``cached_metro_report`` for the rank-0 summary row whose
    ``ISPname`` encodes the coverage dates, e.g.
    ``'2026-05-22 - 2026-05-28 cached'``.

    A fresh BigQuery client's first query can fail with a transient 4xx; this
    retries once and never raises, so the date-range label can't break the rest
    of the render (returns a soft message on failure).

    TODO(v3): this runs the full ``cached_metro_report`` table function just to
    read one date-range string — unnecessarily expensive. In the v3 design the
    date range becomes a lightweight "report"; overhaul then. Not worth fixing
    before v3.
    """
    sql = (f'SELECT ISPname FROM `{dataset}.cached_metro_report`'
           f'("MinRTT", ".*", 1) LIMIT 1')
    df = None
    for _attempt in range(2):
        try:
            df = run_query(client, sql)
            break
        except Exception:
            df = None
    if df is None:
        return "unavailable — click Run again"
    if df.empty:
        return "unknown"
    val = str(df['ISPname'].iloc[0])
    # Strip leading rank prefix "0 " → keep the date range onwards.
    m = re.search(r'(\d{4}-\d{2}-\d{2}.*)', val)
    return m.group(1).strip() if m else val


def asn_regex(isp_names: list) -> str:
    """Build a BQ regex matching any of the ISP names by their ASN (client AS number).

    ISP names in these dashboards follow the format ``"{ASN} {Name}"``, e.g.
    ``"18881 TELEFÔNICA BRASIL S A"``.  The ASN is the leading run of digits.
    Matching on ``^(18881 |28573 )`` — ASN delimited by start-of-field and a
    trailing space — is more stable than matching the full name text.

    The returned string is intended to be wrapped in a :class:`qb.Raw` value so
    that it passes through :func:`qb.interpolate` without further escaping.  The
    SQL template already supplies the surrounding ``^(...)`` via::

        REGEXP_CONTAINS(ISPname, "^(${ClientISP:regex})")

    so the returned value should be ``"18881 |28573 "`` (not ``"^(18881 |28573 )"``).
    """
    parts = []
    for name in isp_names:
        asn = str(name).split()[0]
        if asn.isdigit():
            parts.append(asn + " ")
    return "|".join(parts) if parts else ".*"


# Regex patterns used by make_bulk_sql — compiled once.
_BULK_CTE_SELECT = re.compile(
    r'(siteName,)((?:\s*\n\s*--[^\n]*)*)(\s*FROM\s+`)')
_BULK_GROUP_BY = re.compile(r'(?i)(GROUP BY\s+[^\n]+)')
_BULK_PARTITION = re.compile(r'(?i)\b(PARTITION\s+BY\s+siteName)\b')
_BULK_FINAL_SELECT = re.compile(r'(?i)(siteName,)(\s*\n\s*FROM\s+ISPdata\b)')


def make_bulk_sql(sql: str) -> str:
    """Add ISPname to a histogram panel SQL so one query covers all client ISPs.

    The original SQL is designed for a single client ISP: it filters by
    ``REGEXP_CONTAINS(ISPname, ...)`` and then aggregates by ``siteName`` (the
    M-Lab server), producing one line per server.  When a broad regex matches
    multiple ISPs the ``GROUP BY siteName`` step would collapse them together.

    This function adds ``ISPname`` to:

    1. The CTE ``SELECT`` list — to carry client identity through the GROUP BY.
    2. The ``GROUP BY`` — to keep each client ISP's histogram separate.
    3. Every ``PARTITION BY siteName`` window clause — so PDF/CDF normalisation
       stays per-(server, ISP) pair rather than mixing ISPs.
    4. The final outer ``SELECT`` — so the caller can filter rows by ISP.

    Server selection (``siteRegex`` / ``${region:regex}``) and the ``siteName``
    column itself are unchanged.
    """
    # 1. CTE SELECT: siteName, → siteName,\n    ISPname,  (handles comment lines)
    sql = _BULK_CTE_SELECT.sub(
        lambda m: m.group(1) + '\n    ISPname,' + m.group(2) + m.group(3), sql)
    # 2. GROUP BY: append , ISPname
    sql = _BULK_GROUP_BY.sub(r'\1, ISPname', sql)
    # 3. PARTITION BY siteName → PARTITION BY siteName, ISPname (preserve case)
    sql = _BULK_PARTITION.sub(lambda m: m.group(1) + ', ISPname', sql)
    # 4. Final SELECT: siteName, → siteName,\n    ISPname,  (before FROM ISPdata)
    sql = _BULK_FINAL_SELECT.sub(
        lambda m: m.group(1) + '\n    ISPname,' + m.group(2), sql)
    return sql


_CASE_MODE_RE = re.compile(
    r'\n[ \t]*\bCASE\s+"[^"]+"\s*\n'    # \n + indent + CASE "$mode"\n
    r'\s*WHEN\s+"pdf"\s+THEN\s+(.+)\n'  # WHEN "pdf" THEN <pdf_expr>  → group 1
    r'\s*WHEN\s+"peak"\s+THEN\s+.+\n'   # WHEN "peak" THEN ...        → skip
    r'\s*ELSE\s+(.+)\n'                  # ELSE <cdf_expr>             → group 2
    r'\s*END\s+AS\s+data,',             # END AS data,
    re.IGNORECASE,
)
_MODE_HAVING_RE = re.compile(
    r'\s*OR\s*\("[^"]*"\s*=\s*"cdf"\)',  # OR ("$mode" = "cdf")
    re.IGNORECASE,
)


def make_combined_sql(sql: str) -> str:
    """Replace the mode-dispatch CASE block with explicit pdf and cdf columns.

    Two transformations:

    1. ``CASE "$mode" WHEN "pdf" THEN <P> … ELSE <C> END AS data`` →
       ``<P> AS pdf,`` and ``<C> AS cdf,`` on separate lines.

    2. Remove the ``OR ("$mode" = "cdf")`` clause from HAVING so that all
       histogram bins are returned regardless of binSize — necessary for an
       accurate CDF window function.  At the default binSize=50 this is a
       no-op (thinning is already disabled); at lower binSize values the PDF
       resolution increases slightly, which is acceptable in a combined chart.
    """
    def _replace_case(m):
        pdf = m.group(1).strip()
        cdf = m.group(2).strip()
        return f'\n    {pdf} AS pdf,\n    {cdf} AS cdf,'

    sql = _CASE_MODE_RE.sub(_replace_case, sql)
    sql = _MODE_HAVING_RE.sub('', sql)
    return sql


def _date_str(dt) -> str:
    return dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)[:10]


def _bin_value(bin_ix: pd.Series, field: str) -> pd.Series:
    if 'linear' in field.lower():
        return bin_ix.astype(float)
    if 'fine' in field.lower():
        return 10 ** (bin_ix / 200.0)
    return 10 ** (bin_ix / 50.0)


def _densify(df: pd.DataFrame) -> pd.DataFrame:
    """Zero-fill missing bins within each (siteName, ISPname) pair's own range.

    Experimental / unified backends return sparse histograms (no zero counts).
    This fills the gaps so PDF and CDF are continuous, using each pair's own
    [minBinIX, maxBinIX] range rather than a metro-wide range — avoids forcing
    many zeros onto pairs that naturally cover a smaller range.
    """
    if df.empty:
        return df
    meta = [c for c in ('metro', 'site', 'ASnumber', 'ISPrank') if c in df.columns]
    groups = []
    for (site_name, isp_name), grp in df.groupby(['siteName', 'ISPname'], sort=False):
        min_ix = int(grp['binIX'].min())
        max_ix = int(min(grp['binIX'].max(), min_ix + 1000))
        full = pd.DataFrame({'binIX': range(min_ix, max_ix + 1)})
        merged = full.merge(grp[['binIX', 'hist'] + meta].copy(),
                            on='binIX', how='left')
        merged['hist'] = merged['hist'].fillna(0).astype(float)
        merged['siteName'] = site_name
        merged['ISPname'] = isp_name
        for col in meta:
            merged[col] = grp[col].iloc[0]
        groups.append(merged)
    return pd.concat(groups, ignore_index=True) if groups else df


def _compute_pdfs(df: pd.DataFrame) -> pd.DataFrame:
    """Compute pdf, cdf, n_tests per (siteName, ISPname).  Keeps binIX so the
    caller can apply binSize thinning before dropping it."""
    results = []
    for (site, isp), grp in df.groupby(['siteName', 'ISPname'], sort=False):
        grp = grp.sort_values('bin').copy()
        total = grp['hist'].sum()
        if total == 0:
            continue
        grp['pdf'] = grp['hist'] / total
        grp['cdf'] = grp['hist'].cumsum() / total
        grp['n_tests'] = int(total)
        extra = [c for c in ('metroStart', 'metroEnd') if c in grp.columns]
        results.append(
            grp[['binIX', 'bin', 'pdf', 'cdf', 'siteName', 'ISPname', 'n_tests'] + extra]
        )
    if not results:
        return pd.DataFrame(
            columns=['binIX', 'bin', 'pdf', 'cdf', 'siteName', 'ISPname', 'n_tests'])
    return pd.concat(results, ignore_index=True)


_EMPTY_HIST = pd.DataFrame(
    columns=['bin', 'pdf', 'cdf', 'siteName', 'ISPname', 'n_tests'])


# Module-level cache of raw histogram query results, keyed by SQL text.
# Persists for the life of the kernel so re-renders (changing the ISP
# selection, re-clicking Run) reuse a query instead of re-hitting BQ. The
# field/metric is part of the SQL, so each metric is cached separately.
_HIST_CACHE: dict[str, pd.DataFrame] = {}


def clear_hist_cache() -> None:
    """Drop all cached histogram query results."""
    _HIST_CACHE.clear()


def _cached_query(client, sql: str, cache=None) -> pd.DataFrame:
    """Run ``sql`` once and memoise the raw result by SQL text.

    ``cache`` defaults to the module-level :data:`_HIST_CACHE`; pass an explicit
    dict to scope caching differently (e.g. per-render).  Returns a defensive
    copy so callers may mutate the result without corrupting the cached frame.
    """
    if cache is None:
        cache = _HIST_CACHE
    if sql not in cache:
        cache[sql] = run_query(client, sql)
    return cache[sql].copy()


def fetch_histograms(
    client,
    method: str,
    field: str,
    site_regex: str,
    isp_count: int,
    bin_size: int = 50,
    x_axis: str = "none",
    from_dt=None,
    to_dt=None,
    isp_regex: str | None = None,
    dataset: str = "mlab-collaboration.mm_preproduction",
    cache=None,
) -> pd.DataFrame:
    """Fetch, densify, and return PDF + CDF for one metric across all matching ISPs.

    Dispatches on the **backend token** — the first ``-``-delimited segment of
    ``method`` (``cached`` / ``live`` / ``exp``).  Any trailing modifiers
    (``DS16``, ``DS1C``, ``DS1V``, ``showLocate``, …) are passed through in the
    full ``method`` string but do **not** select the backend, so e.g.
    ``live-DS16`` runs against the live (unified) backend:

    * ``cached``           → ``access_ndt7_cached_histograms(field, site_regex, isp_count)``
      (pre-densified in BQ; 3 args; no dates)
    * ``exp``              → ``experimental_ndt7_isp_histograms(...)``
      (sparse; densified in Python)
    * anything else (live) → ``unified_ndt7_isp_histograms(...)``
      (sparse; densified in Python)

    Raw query results are memoised (see :func:`_cached_query`); ``cache``
    overrides the module-level store.  BQ errors propagate unchanged so they
    surface in the notebook output.

    Returns a DataFrame with columns ``bin``, ``pdf``, ``cdf``, ``siteName``,
    ``ISPname``, ``n_tests``.
    """
    backend = method.split('-', 1)[0].strip().lower()

    if backend == 'cached':
        sql = (f'SELECT * FROM `{dataset}.access_ndt7_cached_histograms`'
               f'("{field}", "{site_regex}", {isp_count})')
        needs_densification = False
    elif backend == 'exp':
        start, end = _date_str(from_dt), _date_str(to_dt)
        sql = (f'SELECT * FROM `{dataset}.experimental_ndt7_isp_histograms`'
               f'("{method}", "{x_axis}", {bin_size}, "{field}", '
               f'DATE "{start}", DATE "{end}", "{site_regex}")')
        needs_densification = True
    else:  # 'live' — and any unrecognised token — use the unified backend
        start, end = _date_str(from_dt), _date_str(to_dt)
        sql = (f'SELECT * FROM `{dataset}.unified_ndt7_isp_histograms`'
               f'("{method}", "{x_axis}", {bin_size}, "{field}", '
               f'DATE "{start}", DATE "{end}", "{site_regex}")')
        needs_densification = True

    df = _cached_query(client, sql, cache)
    if df.empty:
        return _EMPTY_HIST.copy()

    df = df.rename(columns={'SiteName': 'siteName'})

    if isp_regex:
        df = df[df['ISPname'].str.match(f"^(?:{isp_regex})", na=False)]
    if df.empty:
        return _EMPTY_HIST.copy()

    if needs_densification:
        df = _densify(df)

    # Recompute bin from binIX — covers densified zero-rows and normalises
    # any inconsistency between backends.
    df = df.copy()
    df['bin'] = _bin_value(df['binIX'], field)

    # PDF / CDF computed on ALL bins so CDF is accurate; thinning comes after.
    df = _compute_pdfs(df)
    if df.empty:
        return _EMPTY_HIST.copy()

    if bin_size < 50 and 'binIX' in df.columns:
        thinned = (df['binIX'] / 50.0 * bin_size).round().astype(int)
        mask = df['binIX'] == (thinned * 50.0 / bin_size).round().astype(int)
        df = df[mask].copy()

    return df.drop(columns=['binIX'], errors='ignore')


def to_html_sticky(df: pd.DataFrame, **kwargs) -> str:
    """``df.to_html`` with column headers frozen (sticky) during vertical scroll.

    Adds inline ``position: sticky`` styling to each header cell so the labels
    stay pinned while the table body scrolls inside its overflow container.
    Uses inline ``style`` attributes (not a ``<style>`` block) so it survives
    Voilà's DOMPurify sanitisation.  The caller must still wrap the result in a
    scrolling container, e.g. ``<div style="height:500px;overflow:auto">``.
    Extra kwargs pass straight through to ``pandas.DataFrame.to_html`` (e.g.
    ``index=False``, ``na_rep``, ``escape``).
    """
    html = df.to_html(**kwargs)
    th_style = ("position:sticky;top:0;z-index:2;"
                "background:var(--jp-layout-color0,#fff);"
                "box-shadow:inset 0 -1px 0 rgba(128,128,128,.4)")
    return html.replace("<th>", f'<th style="{th_style}">')


def plotly_combined_figure(
    df: pd.DataFrame,
    layout: dict | None = None,
    title: str = "",
    sites: list | None = None,
) -> go.Figure:
    """Build a combined PDF + CDF figure with two vertically offset Y axes.

    Expects a DataFrame with columns ``bin``, ``pdf``, ``cdf``, ``siteName``
    (produced by :func:`make_combined_sql` + :func:`make_bulk_sql`).  One line
    per M-Lab site appears on each sub-axis; the PDF sub-axis sits in the
    bottom 42 % of the plot area, the CDF sub-axis in the top 42 %.  A 16 %
    gap between them prevents overlap.  The legend is keyed to the site names
    and shared between both sub-axes (each site appears once).

    ``sites`` is the full set of servers to force into the legend.  Any server
    in ``sites`` with no rows in ``df`` (zero data points for this ISP) still
    gets a legend entry — an empty trace labelled ``"(0)"`` — so every selected
    server is always shown.  Passing the full server set also keeps the colour
    assignment stable across the per-ISP charts (colours index into the sorted
    union, which is identical for every ISP).
    """
    fig = go.Figure()
    _has = df is not None and not df.empty and 'pdf' in df.columns
    _data_sites = sorted(df['siteName'].dropna().unique(), key=str) if _has else []
    if sites:
        all_sites = sorted({str(s) for s in sites} | set(_data_sites), key=str)
    else:
        all_sites = _data_sites

    if all_sites:
        # Precompute labels/subframes (needs n_tests before either trace loop).
        # A server absent from df gets sub=None → empty trace, "(0)" count.
        labels = {}
        subs   = {}
        for site in all_sites:
            sub = df[df['siteName'] == site] if _has else None
            _rows = sub is not None and len(sub) > 0
            n = int(sub['n_tests'].iloc[0]) if _rows and 'n_tests' in sub.columns else 0
            labels[site] = f"<b>{site} ({n:,})</b>"
            subs[site]   = sub if _rows else None

        # Add CDF traces first so Plotly assigns them colours 0, 1, 2 … from
        # the active template's colorway.
        for site in all_sites:
            _s = subs[site]
            fig.add_trace(go.Scatter(
                x=(_s['bin'] if _s is not None else []),
                y=(_s['cdf'] if _s is not None else []),
                name=labels[site], mode='lines', line=dict(width=2),
                yaxis='y2', legendgroup=labels[site], showlegend=False,
            ))

        # Read the colours Plotly would assign (sequential index into colorway).
        import plotly.io as _pio
        _colorway = list(
            _pio.templates[_pio.templates.default].layout.colorway
            or ['#636EFA','#EF553B','#00CC96','#AB63FA','#FFA15A',
                '#19D3F3','#FF6692','#B6E880','#FF97FF','#FECB52']
        )

        # Add PDF traces with colours that match their CDF counterparts.
        for i, site in enumerate(all_sites):
            color = _colorway[i % len(_colorway)]
            _s = subs[site]
            fig.add_trace(go.Scatter(
                x=(_s['bin'] if _s is not None else []),
                y=(_s['pdf'] if _s is not None else []),
                name=labels[site], mode='lines', line=dict(width=2, color=color),
                yaxis='y', legendgroup=labels[site], showlegend=True,
            ))

    xaxis = copy.deepcopy(layout.get('xaxis', {})) if layout else {}
    # Gap between PDF (bottom) and CDF (top) is [0.44, 0.56] — 12% of plot
    # area. The legend is anchored inside that gap to avoid consuming extra
    # vertical space above or below the subplots.
    fig.update_layout(
        title=dict(text=f"<b>{title}</b>" if title else "",
                   font=dict(size=13), pad=dict(t=0, b=0)),
        height=600,
        showlegend=True,
        legend=dict(
            orientation='h', x=0.5, y=0.50,
            xanchor='center', yanchor='middle',
            font=dict(size=13),
        ),
        margin=dict(l=50, r=55, t=22, b=15),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#212121'),
        xaxis=xaxis,
        yaxis=dict(
            title=dict(text='PDF', font=dict(size=12)),
            domain=[0, 0.44], side='left',
            gridcolor='#333', rangemode='nonnegative',
            tickfont=dict(size=11),
        ),
        yaxis2=dict(
            title=dict(text='CDF', font=dict(size=12)),
            domain=[0.56, 1.0], range=[0, 1],
            side='right', gridcolor='#333',
            tickfont=dict(size=11),
        ),
    )
    return fig


def fleet_map(df: pd.DataFrame) -> go.Figure:
    """World map — one marker per metro, combining metro summary and site detail.

    Row classification (from ``level`` column):
    - ``"Subtotal by Metro"``     — metro aggregate (marker position + cost label)
    - ``"Subtotal by Metro-SKU"`` — per-deployment breakdown (ignored for display)
    - ``"Site"``                  — individual site row (per-site hover lines)

    Hover content per metro:
    - Metro total line: servers, T/day, TB/mo, Mbps, cost  ($/day)
    - Per-site lines (if present): T/day, TB/mo, Mbps  (no cost)

    Markers are coloured by the metro's average Mbps on the Plasma scale.
    """
    df = df.dropna(subset=['lat', 'long']).copy()
    if df.empty:
        return go.Figure()

    metro_rows = df[df['level'] == 'Subtotal by Metro']
    site_rows  = df[df['level'] == 'Site'].copy()
    # For site rows the 'metro' column holds the site code (e.g. 'nbo01');
    # strip to the first 3 characters to recover the metro code.
    site_rows['_metro'] = site_rows['metro'].str[:3]

    all_metros = metro_rows['metro'].dropna().unique()
    if not len(all_metros):
        all_metros = site_rows['_metro'].dropna().unique()

    lats, lons, texts, colors = [], [], [], []

    for metro in sorted(all_metros):
        mr = metro_rows[metro_rows['metro'] == metro]
        sr = site_rows[site_rows['_metro'] == metro].sort_values('TpD', ascending=False)

        # Marker position from the metro aggregate row
        if not mr.empty:
            lat, lon = float(mr['lat'].iloc[0]), float(mr['long'].iloc[0])
            color_val = float(mr['Mbps'].iloc[0])
        elif not sr.empty:
            lat, lon = float(sr['lat'].mean()), float(sr['long'].mean())
            color_val = float(sr['Mbps'].sum())
        else:
            continue

        lines = []

        # --- Metro total ---
        if not mr.empty:
            r   = mr.iloc[0]
            loc = str(r.get('Loc', '') or '').strip()
            lines.append(f"<b>{metro} — {loc}</b>")
            lines.append(
                f"{int(r['servers'])} servers | "
                f"{r['TpD']:,.0f} T/day | "
                f"{r['TBpM']:,.1f} TB/mo | "
                f"{r['Mbps']:,.0f} Mbps"
            )
            lines.append(f"${r['DpD']:,.0f}/day")

        # --- Individual sites (site code is in the 'metro' column for Site rows) ---
        if not sr.empty:
            lines.append("─" * 28)
            for _, s in sr.iterrows():
                site_id = str(s.get('metro', '') or metro)
                lines.append(
                    f"  {site_id}: "
                    f"{s['TpD']:,.0f} T/day | "
                    f"{s['TBpM']:,.1f} TB/mo | "
                    f"{s['Mbps']:,.0f} Mbps"
                )

        lats.append(lat)
        lons.append(lon)
        texts.append("<br>".join(lines))
        colors.append(color_val)

    if not lats:
        return go.Figure()

    fig = go.Figure(go.Scattergeo(
        lat=lats, lon=lons, text=texts,
        mode='markers',
        marker=dict(
            size=8,
            color=colors,
            colorscale='Plasma',
            showscale=True,
            colorbar=dict(title="Mbps", thickness=12, len=0.6),
            opacity=0.85,
        ),
        hoverinfo='text',
    ))
    fig.update_layout(
        geo=dict(
            showland=True,      landcolor='#1a1a2e',
            showocean=True,     oceancolor='#0f0e17',
            showcountries=True, countrycolor='#444',
            showcoastlines=True, coastlinecolor='#444',
            bgcolor='rgba(0,0,0,0)',
            projection_type='natural earth',
        ),
        autosize=True,   # width fills the page; height fixed below
        height=700,
        margin=dict(l=0, r=0, t=5, b=0),
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#212121'),
        showlegend=False,
    )
    return fig


def metro_barchart_clickable(
    df: pd.DataFrame,
    isp_count: int | str = 5,
    target_notebook: str = "regional_details_dashboard",
    link_widget=None,
) -> "go.FigureWidget":
    """Metro bar chart with click-to-navigate to Regional Details.

    Clicking a bar updates *link_widget* (an ``ipywidgets.HTML``) with a
    navigation link — no Javascript injection required, works in Voilà.
    If *link_widget* is None a new one is created (caller should display it).
    """
    import re as _re
    import ipywidgets as _ipyw

    if link_widget is None:
        link_widget = _ipyw.HTML()

    fw = go.FigureWidget(metro_barchart(df))

    def _on_click(trace, points, state):
        if not points.point_inds:
            return
        name = str(points.xs[0])
        m = _re.search(r'\(([a-z]{3})\)', name)
        if not m:
            return
        anchor = m.group(1)
        url = (f"/voila/render/{target_notebook}.ipynb"
               f"?anchor={anchor}&ISPcount={isp_count}")
        link_widget.value = (
            f'<p style="margin:6px 0;font-size:14px">'
            f'&#8599; <a href="{url}" target="_blank">'
            f'<b>{name}</b> — Regional Details (ISPcount={isp_count})</a></p>'
        )

    for trace in fw.data:
        trace.on_click(_on_click)

    return fw


def metro_barchart(
    df: pd.DataFrame,
    title: str = "",
    isp_count: int | str | None = None,
    target_notebook: str = "regional_details_dashboard",
) -> go.Figure:
    """Grouped bar chart of metro-level KS distance and spread scores.

    When ``isp_count`` is supplied, each bar's hover tooltip contains a
    clickable ``<a href>`` link to the Regional Details notebook pre-set to
    that metro and ISP count.  This works in Voilà without Python callbacks
    because the link is rendered as HTML directly in the browser.
    """
    import re as _re

    def _url(name: str) -> str | None:
        m = _re.search(r'\(([a-z]{3})\)', str(name))
        if not m or isp_count is None:
            return ""
        return (f"/voila/render/{target_notebook}.ipynb"
                f"?anchor={m.group(1)}&ISPcount={isp_count}")

    fig = go.Figure()
    if df is not None and not df.empty:
        urls = [_url(n) for n in df['name']]
        has_links = isp_count is not None
        _nav_hint = ''  # navigation via companion link list; hover is display-only

        fig.add_trace(go.Bar(
            x=df['name'], y=df['scaledKSdistance'],
            name='KSdistance × 10',
            marker_color='steelblue',
            customdata=urls,
            hovertemplate=(
                '<b>%{x}</b><br>KSdistance×10: %{y:.3f}'
                + _nav_hint + '<extra></extra>'
            ),
        ))
        if 'Spread' in df.columns:
            fig.add_trace(go.Bar(
                x=df['name'], y=df['Spread'],
                name='Spread',
                marker_color='coral',
                customdata=urls,
                hovertemplate=(
                    '<b>%{x}</b><br>Spread: %{y:.3f}'
                    + _nav_hint + '<extra></extra>'
                ),
            ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13)) if title else None,
        barmode='group',
        height=500,
        xaxis=dict(tickangle=-45, tickfont=dict(size=10)),
        # Clip at 12: KSdistance×10 tops out at 10; Spread is an unbounded ratio,
        # so outliers are capped here to keep the scale readable (true value
        # still shown on hover).
        yaxis=dict(range=[0, 12], gridcolor='#333',
                   title=dict(font=dict(size=12)), tickfont=dict(size=11)),
        margin=dict(l=50, r=20, t=35, b=130),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#212121'),
        legend=dict(orientation='h', y=1.02, x=0, font=dict(size=13)),
        showlegend=True,
    )
    return fig


def metro_nav_html(
    df: pd.DataFrame,
    isp_count: int | str = 5,
    target_notebook: str = "regional_details_dashboard",
) -> str:
    """Collapsible HTML list of metro navigation links for use beside a bar chart.

    Uses ``<details>/<summary>`` — no JavaScript required, works in any browser.
    The user expands the list and clicks a metro to open Regional Details.
    """
    import re as _re

    rows = []
    for name in df['name'].dropna().unique():
        m = _re.search(r'\(([a-z]{3})\)', str(name))
        if not m:
            continue
        url = (f"/voila/render/{target_notebook}.ipynb"
               f"?anchor={m.group(1)}&ISPcount={isp_count}")
        rows.append(
            f'<li style="white-space:nowrap">'
            f'<a href="{url}" target="_blank">{name}</a></li>'
        )
    if not rows:
        return ""
    return (
        '<div style="margin-top:6px;font-size:11px">'
        '<span style="color:grey">↗ Open in Regional Details:</span>'
        '<ul style="columns:3;margin:2px 0;padding-left:18px">'
        + "\n".join(rows)
        + "</ul></div>"
    )


def regional_details_url(
    anchor: str,
    sites: list[str] | None = None,
    isp_values: list[str] | None = None,
    target: str = "regional_details_dashboard",
) -> str:
    """Build a Voilà-relative URL for the Regional Details notebook.

    *isp_values* is a list of ``"{ASnumber} {ISPname}"`` strings as returned
    by BQ.  Only the AS number is included in the href; the name is for display
    only (see :func:`isp_link_text`).
    """
    params = [f"anchor={anchor}"]
    if sites:
        params.append("sites=" + ",".join(sites))
    if isp_values:
        asns = [v.split(" ")[0] for v in isp_values]
        params.append("ISPs=" + ",".join(asns))
    return f"/voila/render/{target}.ipynb?" + "&".join(params)


def isp_link_text(isp_values: list[str]) -> str:
    """Return ISP display names (everything after the AS number) for anchor text."""
    return ", ".join(" ".join(v.split(" ")[1:]) for v in isp_values)


def breadcrumb_to_url(
    breadcrumb: str,
    target: str = "regional_details_dashboard",
) -> str:
    """Parse a breadcrumb string and return a Voilà-relative URL for Regional Details.

    Breadcrumb format (space-delimited): ``site1 site2 ASnumber [ISPname]``
    Only the AS number is included in the URL; the ISPname is for display only.
    Returns an empty string if the breadcrumb is empty or malformed.
    """
    if not breadcrumb or not str(breadcrumb).strip():
        return ""
    parts = str(breadcrumb).strip().split(" ", 3)
    if len(parts) < 3:
        return ""
    sites  = [parts[0], parts[1]]
    asn    = parts[2]
    anchor = parts[0][:3]
    return regional_details_url(anchor, sites=sites, isp_values=[asn],
                                target=target)


def run_competition_report(
    client,
    report_type: str = "minRTT",
    method: str = "cached",
    org: str = ".*",
    radius: int = 100,
    isp_count: int = 5,
    from_dt=None,
    to_dt=None,
    dataset: str = "mlab-collaboration.mm_preproduction",
) -> pd.DataFrame:
    """Run ``minRTT_competition_report`` or ``throughput_competition_report``.

    ``report_type`` selects the BQ function (``'minRTT'`` or ``'throughput'``).
    The Breadcrumb column is retained; the render template parses its
    space-delimited contents into a navigation link.
    """
    start = _date_str(from_dt)
    end   = _date_str(to_dt)
    fn = f"{report_type}_competition_report"
    sql = (
        f'SELECT * FROM `{dataset}.{fn}`'
        f'("{method}", "{start}", "{end}", "{org}", {radius}, {isp_count})'
    )
    return run_query(client, sql)


def run_calibration_report(
    client,
    method: str = "cached",
    x_axis: str = "none",
    bin_size: int = 50,
    field: str = "MeanThroughputMbps",
    from_dt=None,
    to_dt=None,
    region_regex: str = ".*",
    radius: int = 100,
    isp_count: int = 5,
    dataset: str = "mlab-collaboration.mm_preproduction",
) -> pd.DataFrame:
    """Run ``calibration_report`` and return all rows as a DataFrame."""
    start = _date_str(from_dt)
    end   = _date_str(to_dt)
    sql = (
        f'SELECT * FROM `{dataset}.calibration_report`'
        f'("{method}", "{x_axis}", {bin_size}, "{field}",'
        f' DATE "{start}", DATE "{end}",'
        f' "^({region_regex})", {radius}, {isp_count})'
    )
    return run_query(client, sql)


def plotly_calibration_scatter(df: pd.DataFrame) -> go.Figure:
    """Scatter of KSdistance vs Ratio for calibration report data.

    Points with Ratio > 2 are clamped to x=2 and shown with a triangle-up
    marker so the viewer knows they exceed the bounding box.
    """
    fig = go.Figure()
    if df is None or df.empty:
        return fig

    ratio_col = "Ratio" if "Ratio" in df.columns else "ratio"
    ks_col    = "KSdistance" if "KSdistance" in df.columns else "ksdistance"
    name_col  = next((c for c in ("name", "Name", "targetSite") if c in df.columns), None)

    raw   = df[ratio_col].astype(float)
    ks    = df[ks_col].astype(float)
    capped = raw.clip(upper=2.0)
    is_clamped = raw > 2.0

    def _hover(mask):
        parts = ks[mask].round(4).astype(str).radd("KSdistance: ")
        parts = parts + "<br>Ratio: " + raw[mask].round(4).astype(str)
        if name_col:
            parts = df.loc[mask, name_col].astype(str) + "<br>" + parts
        return parts

    if (~is_clamped).any():
        fig.add_trace(go.Scatter(
            x=capped[~is_clamped], y=ks[~is_clamped],
            mode="markers",
            marker=dict(size=7, symbol="circle", color="steelblue", opacity=0.75),
            text=_hover(~is_clamped),
            hoverinfo="text",
            name="ratio ≤ 2",
        ))

    if is_clamped.any():
        fig.add_trace(go.Scatter(
            x=capped[is_clamped], y=ks[is_clamped],
            mode="markers",
            marker=dict(size=9, symbol="triangle-up", color="coral", opacity=0.9),
            text=_hover(is_clamped),
            hoverinfo="text",
            name="ratio > 2 (clamped to 2)",
        ))

    fig.update_layout(
        xaxis=dict(title=dict(text="Ratio (capped at 2)", font=dict(size=12)),
                   range=[1.0, 2.05], gridcolor="#333", tickfont=dict(size=11)),
        yaxis=dict(title=dict(text="KS Distance", font=dict(size=12)),
                   gridcolor="#333", rangemode="nonnegative", tickfont=dict(size=11)),
        height=450,
        margin=dict(l=55, r=20, t=30, b=45),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#212121"),
        showlegend=True,
        legend=dict(orientation="h", y=1.02, x=0, font=dict(size=13)),
    )
    return fig


def plotly_grouped_figure(
    df: pd.DataFrame,
    layout: dict | None = None,
    title: str = "",
) -> go.Figure:
    """Rebuild an ``ae3e-plotly-panel`` figure from a 3-column result frame.

    Replicates the dashboards' client-side panel script: the first column is x,
    the second is y, and the third is a series name (the M-Lab site). Rows are
    grouped into one line trace per series, sorted by series name.
    """
    fig = go.Figure()
    if df is not None and not df.empty and df.shape[1] >= 3:
        xcol, ycol, namecol = df.columns[:3]
        for name in sorted(df[namecol].dropna().unique(), key=str):
            sub = df[df[namecol] == name]
            fig.add_trace(
                go.Scatter(
                    x=sub[xcol], y=sub[ycol], name=str(name),
                    mode="lines", line=dict(width=2),
                )
            )
    if layout:
        fig.update_layout(**copy.deepcopy(layout))
    fig.update_layout(title=title, height=380,
                      margin=dict(l=50, r=20, t=40, b=40),
                      showlegend=True)
    return fig
