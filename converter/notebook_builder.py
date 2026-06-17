"""Assemble a Jupyter notebook (.ipynb) from a parsed Grafana dashboard.

The generated notebook keeps each panel's BigQuery SQL inline so it reads as a
faithful conversion, and uses the :mod:`converter.runtime`,
:mod:`converter.query_builder`, and :mod:`converter.widget_builder` helpers for
the mechanical work (interpolation, querying, widgets, plotting).

Panel handling mirrors Grafana semantics:

* ``text`` panels  -> the intro markdown cell.
* ``row`` panels with ``repeat`` start a *repeat group*: the panels that follow
  (until the next row) are rendered once per selected value of that variable
  (here ``ClientISP``).
* ``table`` panels that reference a BigQuery resource -> a results table.
* ``table`` panels with no resource (the "diagnostic" echo panel) -> a small
  table of the resolved variable values, built in Python.
* ``ae3e-plotly-panel`` -> a Plotly figure (grouped into one line per site).

Output is always written under ``notebooks.stage/`` (rerun-safe; the user merges
into ``notebooks/`` by hand).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _compact_json(obj: object) -> str:
    """Pretty-print JSON with indent=2, then collapse simple dicts to one line.

    Two patterns are collapsed:
    * ``{ "text": "...", "value": "..." }`` — dropdown option entries
    * ``{ "value": "..." }``                — current/default sentinel dicts

    Everything else (nested structures, long SQL strings) stays expanded.
    """
    raw = json.dumps(obj, indent=2, ensure_ascii=False)
    # Collapse {"text": "...", "value": "..."} option dicts
    raw = re.sub(
        r'\{\s*\n\s*"text":\s*("(?:[^"\\]|\\.)*"),\s*\n\s*"value":\s*("(?:[^"\\]|\\.)*")\s*\n\s*\}',
        lambda m: '{ "text": ' + m.group(1) + ', "value": ' + m.group(2) + ' }',
        raw,
    )
    # Collapse {"value": "..."} current/default dicts
    raw = re.sub(
        r'\{\s*\n\s*"value":\s*("(?:[^"\\]|\\.)*")\s*\n\s*\}',
        lambda m: '{ "value": ' + m.group(1) + ' }',
        raw,
    )
    return raw

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from .widget_builder import _referenced_vars


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    return s or "dashboard"


def _panels_to_python(panels: list[dict]) -> str:
    """Render panel specs as a Python list literal.

    Multi-line SQL strings use raw triple-quoted strings so they're readable
    in the notebook cell.  Other fields use compact repr/JSON.
    Python's str.format() substitutes this verbatim without re-scanning for
    ``{...}`` patterns, so ``${dataset}`` etc. in SQL pass through safely.
    """
    def _val(key, v):
        if key == 'sql' and isinstance(v, str) and '\n' in v:
            return f'r"""\n{v.strip()}\n""".strip()'
        if isinstance(v, bool):
            return 'True' if v else 'False'
        if v is None:
            return 'None'
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return repr(v)
        # dict / list: compact JSON (no multiline needed for layout etc.)
        return json.dumps(v)

    lines = ['[']
    for panel in panels:
        lines.append('  {')
        for k, v in panel.items():
            lines.append(f'    {k!r}: {_val(k, v)},')
        lines.append('  },')
    lines.append(']')
    return '\n'.join(lines)


# Pure browser-side Plotly.js click handler — no Python callback needed.
# Attached via a <script> tag so it works in Voilà.  Uses a sentinel in _RENDER
# to avoid brace-escaping in str.format().


def _panel_spec(panel, var_names: set[str]) -> dict:
    target = panel.targets[0] if panel.targets else None
    sql = target.raw_sql if target else ""
    refs = _referenced_vars(sql, var_names)
    return {
        "id": panel.panel_id,
        "title": panel.title,
        "type": panel.type,
        "sql": sql,
        "layout": panel.plotly_layout or {},
        # The 4th-column plotly panel keys off the `field` variable, which is
        # "none" by default and unsupported on cached data -> skip when none.
        "skip_if_field_none": "field" in refs,
    }


# Options supported by access_ndt7_cached_histograms (partition map in its DDL).
# Used to prune dropdown choices that would silently fall back to MinRTT.
_CACHED_TABLE_FIELDS = {'MinRTT', 'MeanThroughputMbps', 'LossRate', 'linearMinRTT'}

# Per-metric Plotly x-axis layout.  Ordered — determines left-to-right figure order.
_METRIC_LAYOUTS = {
    "MeanThroughputMbps": {"type": "log",    "autorange": False, "range": [-0.3, 3.3], "gridcolor": "#333"},
    "MinRTT":             {"type": "log",    "autorange": False, "range": [-0.3, 3.0], "gridcolor": "#333"},
    "linearMinRTT":       {"type": "linear", "autorange": False, "range": [0, 300],    "gridcolor": "#333"},
    "LossRate":           {"type": "log",    "autorange": True,                        "gridcolor": "#333"},
}

# Python code injected into the exp render() function to build the composite
# method string from the sub-selector widgets before any BQ calls are made.
# Substituted verbatim into the notebook cell via str.format(); f-string
# expressions inside are not processed by .format() so no escaping is needed.
# Code injected before the table display in fleet notebooks.
# Renders a global map when metros or sites are in the display selection.
_FLEET_BEFORE_TABLE = """\
                        _display_sel = ctx.get("display") or []
                        if isinstance(_display_sel, str): _display_sel = [_display_sel]
                        if any(d in _display_sel for d in ("metros", "sites")):
                            _map_df = _df[_df["lat"].notna() & _df["long"].notna()].copy()
                            if not _map_df.empty:
                                display(go.FigureWidget(rt.fleet_map(_map_df)))
"""

_EXP_METHOD_PREAMBLE = """\
    # Build composite BQ method string from sub-selector widgets.
    _ms    = ctx.get("methodsrc", "cached")
    _loc   = ctx.get("locate", "showLocate")
    _sub   = ctx.get("sub_method", "default")
    _extra = (ctx.get("extra_flags") or "").strip()
    if _sub == "showName=":
        _sub = f"showName={ctx.get('clientname', '')}"
    if _ms != "cached":
        _parts = [_ms, _loc, _sub]
        if _extra:
            _parts.append(_extra)
        ctx["method"] = "-".join(p for p in _parts if p and p != "default")
    else:
        ctx["method"] = "cached"
"""


def _filter_for_cached(variables: list[dict]) -> list[dict]:
    """Prune dropdown options that are unsupported when method=cached.

    * ``method`` — keep only ``cached`` (live/experimental have no backend).
    * ``table_field`` — keep only the four fields the cache partitions on;
      others silently fall back to MinRTT.
    * ``field`` (Fourth column, panel 133) — keep only cache-compatible values.
    """
    out = []
    for v in list(variables):
        v = dict(v)
        name = v['name']
        if name == 'mode':
            continue  # PDF and CDF are always shown together; selector removed
        elif name == 'method':
            v['options'] = [o for o in v['options'] if o['value'] == 'cached']
            v['current'] = {'value': 'cached'}
        elif name == 'region':
            v['default_select'] = 'all'   # select all servers by default
        elif name == 'ClientISP':
            v['default_select'] = 'half'  # select first half of ranked ISPs
        elif name == 'table_field':
            v['options'] = [o for o in v['options']
                            if o['value'] in _CACHED_TABLE_FIELDS]
            cur = (v.get('current') or {}).get('value')
            if cur not in _CACHED_TABLE_FIELDS:
                v['current'] = {'value': v['options'][0]['value']} if v['options'] else {}
        elif name == 'field':
            continue  # superseded by the metrics multi-select chooser
        elif name == 'verbose':
            # Replace the true/false toggle with a three-way table-style chooser.
            v['name'] = 'table_style'
            v['label'] = 'Table'
            v['description'] = 'Summary table display style.'
            v['options'] = [
                {'text': 'none',    'value': 'none'},
                {'text': 'Summary', 'value': 'Summary'},
                {'text': 'Verbose', 'value': 'Verbose'},
            ]
            v['current'] = {'value': 'none'}
        out.append(v)

    # table_style (whether to show) should precede table_field (which metric).
    # The dashboard has them in the opposite order.
    _ts = next((i for i, v in enumerate(out) if v['name'] == 'table_style'), None)
    _tf = next((i for i, v in enumerate(out) if v['name'] == 'table_field'), None)
    if _ts is not None and _tf is not None and _ts > _tf:
        out[_ts], out[_tf] = out[_tf], out[_ts]

    return out


def _filter_for_exp(variables: list[dict]) -> list[dict]:
    """Variable filter for the experimental dashboard.

    Matches prod layout: table_style chooser, same selector styles for
    region/ClientISP/metrics.  Sub-method and extra_flags appear first,
    before anchor.
    """
    out = []
    for v in list(variables):
        v = dict(v)
        name = v['name']
        if name in ('mode', 'method', 'field'):
            continue
        elif name == 'verbose':
            # Same three-way table_style chooser as prod.
            v['name'] = 'table_style'
            v['label'] = 'Table'
            v['description'] = 'Summary table display style.'
            v['options'] = [
                {'text': 'none',    'value': 'none'},
                {'text': 'Summary', 'value': 'Summary'},
                {'text': 'Verbose', 'value': 'Verbose'},
            ]
            v['current'] = {'value': 'none'}
        elif name == 'region':
            v['default_select'] = 'all'
        elif name == 'ClientISP':
            v['default_select'] = 'half'
        out.append(v)

    # table_style before table_field (same ordering as prod).
    _ts = next((i for i, v in enumerate(out) if v['name'] == 'table_style'), None)
    _tf = next((i for i, v in enumerate(out) if v['name'] == 'table_field'), None)
    if _ts is not None and _tf is not None and _ts > _tf:
        out[_ts], out[_tf] = out[_tf], out[_ts]

    # Sub-method and extra_flags at the top, before anchor.
    out.insert(0, {
        "name": "extra_flags",
        "type": "textbox",
        "label": "Extra flags",
        "description": "Arbitrary subselector flags appended to the method string.",
        "hide": 0,
        "multi": False,
        "options": [],
        "current": {"value": ""},
        "query_sql": None,
    })
    out.insert(0, {
        "name": "sub_method",
        "type": "custom",
        "label": "Sub-method",
        "description": "Method sub-selector flag passed to the BQ backend.",
        "hide": 0,
        "multi": False,
        "options": [
            {"text": "default",    "value": "default"},
            {"text": "showIPv",    "value": "showIPv"},
            {"text": "showEarly",  "value": "showEarly"},
            {"text": "showNames",  "value": "showNames"},
            {"text": "showName=…", "value": "showName="},
        ],
        "current": {"value": "default"},
        "query_sql": None,
    })
    return out


def _filter_for_fleet(variables: list[dict]) -> list[dict]:
    """Minimal filter for table-only dashboards (e.g. Fleet and Egress).

    Transformations:
    - Expand Grafana's ``$__all`` sentinel to explicit option values.
    - Override ``endDate`` with a dynamic default so it always opens on the
      date two days before the notebook is launched, not the stale Grafana value.
    """
    out = []
    for v in variables:
        v = dict(v)
        cur = (v.get("current") or {}).get("value")
        if v.get("multi") and isinstance(cur, list) and "$__all" in cur:
            v["options"] = [o for o in v.get("options", [])
                            if o["value"] != "$__all"]
            v["current"] = {"value": ["metros"]}
        elif v["name"] == "endDate":
            v["dynamic_default"] = "(date.today() - timedelta(days=2)).isoformat()"
        out.append(v)
    return out


def _filter_for_barchart(variables: list[dict]) -> list[dict]:
    """Variable filter for bar-chart-only dashboards (e.g. Global Metro Bar Chart).

    Drops dashboard variables that are Grafana-specific and not useful in a
    notebook: Prometheus datasource reference, and drill-down URL constants.
    Keeps ``verbose`` as-is (it controls single-site metro inclusion, not
    summary-table verbosity).
    """
    drop = {'prometheus', 'detailURL'}
    return [dict(v) for v in variables if v['name'] not in drop]


def _add_metrics_var(variables: list[dict]) -> list[dict]:
    """Insert the metrics multi-select chooser before binSize."""
    metrics_var = {
        "name": "metrics",
        "type": "custom",
        "label": "Metrics",
        "description": "Select which metrics to plot.",
        "hide": 0,
        "multi": True,
        "options": [{"text": k, "value": k} for k in _METRIC_LAYOUTS],
        "current": {"value": ["MeanThroughputMbps"]},
        "query_sql": None,
    }
    idx = next((i for i, v in enumerate(variables) if v["name"] == "binSize"),
               len(variables))
    variables.insert(idx, metrics_var)
    return variables


def serialize_variables(dashboard, flavor: str = 'prod') -> list[dict]:
    """Bake dashboard variable metadata into a plain list of dicts.

    ``flavor`` selects the variable filter: ``'prod'`` applies
    :func:`_filter_for_cached`; ``'exp'`` applies :func:`_filter_for_exp`.
    """
    out = []
    for v in dashboard.variables:
        out.append({
            "name": v.name,
            "type": v.type,
            "label": v.label or "",
            "description": v.description or "",
            "hide": v.hide,
            "multi": v.multi,
            "options": [{"text": o.get("text", o.get("value", "")),
                         "value": o.get("value", "")}
                        for o in (v.options or [])],
            "current": {"value": v.current.get("value")} if v.current else {},
            "query_sql": v.query_sql,
        })
    if flavor == 'exp':
        filtered = _filter_for_exp(out)
    elif flavor == 'fleet':
        filtered = _filter_for_fleet(out)
        return filtered          # no metrics chooser for table-only dashboards
    elif flavor == 'barchart':
        filtered = _filter_for_barchart(out)
        return filtered          # no metrics chooser for bar-chart dashboards
    else:
        filtered = _filter_for_cached(out)
    return _add_metrics_var(filtered)


def classify_panels(dashboard):
    """Split panels into intro markdown, summary tables, per-repeat charts."""
    var_names = {v.name for v in dashboard.variables}
    intro: list[str] = []
    summary: list[dict] = []
    repeat_panels: list[dict] = []
    diagnostic_title = None
    repeat_var = None
    current_repeat = None

    for p in dashboard.panels:
        if p.type == "text":
            if p.content:
                intro.append(p.content)
            continue
        if p.type == "row":
            current_repeat = p.repeat
            if p.repeat:
                repeat_var = p.repeat
            continue
        has_refs = any(t.refs for t in p.targets)
        if p.type == "table" and not has_refs:
            diagnostic_title = p.title  # the "echo" diagnostic panel
            continue
        spec = _panel_spec(p, var_names)
        if current_repeat:
            repeat_panels.append(spec)
        else:
            summary.append(spec)

    return {
        "intro": "\n\n".join(intro),
        "summary": summary,
        "repeat_panels": repeat_panels,
        "repeat_var": repeat_var,
        "diagnostic_title": diagnostic_title or "Diagnostics",
    }


# --- cell source templates ------------------------------------------------

_SETUP = '''\
# --- Setup ---
import os, sys, json
from datetime import datetime, date, time, timedelta, timezone

# Locate the repo root (directory containing `converter/`) regardless of where
# Voila/Jupyter is launched from.
_root = os.path.abspath(os.getcwd())
while _root != os.path.dirname(_root) and not os.path.isdir(os.path.join(_root, "converter")):
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

import ipywidgets as widgets
import plotly.graph_objects as go
import pandas as pd
from IPython.display import display, HTML, Markdown

from converter import query_builder as qb, runtime as rt
from converter.widget_builder import Controls

client = rt.bq_client()

# Dashboard variable metadata baked in at conversion time.
VARIABLES = json.loads(r"""
{variables_json}
""")
'''

_URL_PARAMS = '''\
# --- URL parameter presets (webapp mode) ---
# Voila injects the request query string into os.environ["QUERY_STRING"] before
# executing the notebook.  get_query_string() also handles the preheat-kernel
# case (blocks until the request arrives).  Falls back gracefully in plain
# Jupyter where neither is set.
# For scripted or test overrides, set DASH_PRESETS to a JSON object.
import urllib.parse

url_params = {}
try:
    from voila.utils import get_query_string
    _qs = get_query_string() or ""
    for _k, _vs in urllib.parse.parse_qs(_qs).items():
        url_params[_k] = _vs[0] if len(_vs) == 1 else _vs
except Exception:
    pass

_env = os.environ.get("DASH_PRESETS")
if _env:
    url_params.update(json.loads(_env))
'''

# Date-picker block for exp/live flavors; other flavors get the null version.
_DATE_PICKERS_EXP = """\

# Date range for experimental/live backends — ignored when method=cached.
# Defaults to one week ending on the most recent Sunday (UTC).
_today_utc  = datetime.now(timezone.utc).date()
_days_back  = (_today_utc.weekday() + 1) % 7   # 0 on Sunday, 1 on Monday …
_end_date   = _today_utc - timedelta(days=_days_back)
_start_date = _end_date  - timedelta(days=6)
w_to   = widgets.DatePicker(value=_end_date,   description='End (UTC)',
                             style={"description_width": "90px"})
w_from = widgets.DatePicker(value=_start_date, description='Start (UTC)',
                             style={"description_width": "90px"})
"""
_DATE_PICKERS_NONE = "\nw_from = w_to = None\n"

_CONTROLS = '''\
# --- Dashboard controls (dropdowns; query-backed ones are chained) ---
ctrl = Controls(VARIABLES, client, presets=url_params)
w_run = widgets.Button(description="Run / Refresh", button_style="primary", icon="play")
{date_pickers}_date_label = widgets.HTML('')   # filled from query results after Run
'''

_RENDER = '''\
# --- Panels (converted from the dashboard) ---
SUMMARY_PANELS  = {summary_panels_python}
METRIC_LAYOUTS  = json.loads(r"""{metric_layouts_json}""")
REPEAT_VAR      = {repeat_var!r}

out = widgets.Output()


def _diagnostics(ctx):
    rows = [(k, ", ".join(v) if isinstance(v, list) else str(v))
            for k, v in ctx.items()]
    return pd.DataFrame(rows, columns=["variable", "value"])


def render(_=None):
    ctx = ctrl.context()
{method_preamble}    to_dt   = (datetime.combine(w_to.value,   time(), tzinfo=timezone.utc)
               if w_to   and w_to.value   else datetime.now(timezone.utc))
    from_dt = (datetime.combine(w_from.value, time(), tzinfo=timezone.utc)
               if w_from and w_from.value else to_dt - timedelta(days=7))
    cache = {{}}

    def query(sql):
        if sql not in cache:
            cache[sql] = rt.run_query(client, sql)
        return cache[sql]

    out.clear_output(wait=True)
    with out:
        # Default to "Summary" when table_style is absent (e.g. fleet dashboard).
        table_style = ctx.get("table_style",
                               "Summary" if SUMMARY_PANELS else "none")
        if table_style != "none":
            _sctx = dict(ctx)
            if "table_style" in ctx:
                # Prod: map table_style → verbose flag expected by regional_report SQL.
                _sctx["verbose"] = "true" if table_style == "Verbose" else "false"
            # Other flavors (barchart, fleet) pass verbose directly from ctx.
            for p in SUMMARY_PANELS:
                display(Markdown("### " + qb.interpolate(p["title"], ctx)))
                sql = qb.interpolate(p["sql"], _sctx, from_dt=from_dt, to_dt=to_dt)
                try:
                    _df = query(sql)
                except Exception as exc:
                    display(HTML(f"<pre>query failed: {{exc}}</pre>"))
                    _df = None
                if _df is not None:
                    if p.get("type") == "barchart":
                        _fig = rt.metro_barchart(
                            _df, isp_count=ctx.get("ISPcount", "5"))
                        _fig._config = {{"responsive": False}}
                        display(_fig)
                        display(HTML(rt.metro_nav_html(
                            _df, isp_count=ctx.get("ISPcount", "5"))))
                    else:
{before_table}                        display(HTML(
                            '<div style="height:500px;overflow:auto">'
                            + _df.to_html(index=False, na_rep="")
                            + '</div>'
                        ))

        repeats = ctx.get(REPEAT_VAR) or []
        if isinstance(repeats, str):
            repeats = [repeats]

        selected_metrics = ctx.get("metrics") or []
        if isinstance(selected_metrics, str):
            selected_metrics = [selected_metrics]

        # One Python call per selected metric fetches data for all client ISPs.
        bulk_by_metric = {{}}
        _site_regex = qb.format_regex(ctx.get("region") or [])
        _isp_regex  = rt.asn_regex(repeats)
        for metric in selected_metrics:
            try:
                bulk_by_metric[metric] = rt.fetch_histograms(
                    client,
                    method=ctx.get("method", "cached"),
                    field=metric,
                    site_regex=_site_regex,
                    isp_count=int(ctx.get("ISPcount", 10)),
                    bin_size=int(ctx.get("binSize", 50)),
                    x_axis=ctx.get("xAxis", "none"),
                    from_dt=from_dt,
                    to_dt=to_dt,
                    isp_regex=_isp_regex,
                    dataset=ctx.get("dataset",
                                    "mlab-collaboration.mm_preproduction"),
                )
            except Exception as exc:
                bulk_by_metric[metric] = exc

        # Update cached date range label from metroStart/metroEnd in query results.
        for _mdf in bulk_by_metric.values():
            if isinstance(_mdf, pd.DataFrame) and 'metroStart' in _mdf.columns:
                _s = pd.to_datetime(_mdf['metroStart'].dropna().min()).date()
                _e = pd.to_datetime(_mdf['metroEnd'].dropna().max()).date()
                _date_label.value = (
                    '<div style="font-size:12px;color:grey;margin:2px 0">'
                    '<b>Cached data:</b> ' + str(_s) + ' – ' + str(_e) + '</div>')
                break

        for value in repeats:
            asn = str(value).split()[0]
            display(HTML(f"<h3>{{REPEAT_VAR}}: {{value}}</h3>"))
            figs = []
            for metric in selected_metrics:
                df_all = bulk_by_metric.get(metric)
                if isinstance(df_all, Exception):
                    figs.append(widgets.HTML(f"<b>{{metric}}</b><pre>{{df_all}}</pre>"))
                    continue
                df = df_all[df_all["ISPname"].str.startswith(asn + " ")]
                try:
                    fig = rt.plotly_combined_figure(
                        df, {{"xaxis": METRIC_LAYOUTS.get(metric, {{}})}}, title=metric)
                    figs.append(go.FigureWidget(fig))
                except Exception as exc:
                    figs.append(widgets.HTML(f"<b>{{metric}}</b><pre>{{exc}}</pre>"))
            if figs:
                display(widgets.HBox(figs, layout=widgets.Layout(flex_flow="row wrap")))

        _diag_out = widgets.Output()
        with _diag_out:
            display(_diagnostics(ctx))
        _diag_acc = widgets.Accordion(children=[_diag_out])
        _diag_acc.set_title(0, 'Selector Diagnostics')
        _diag_acc.selected_index = None   # collapsed by default
        display(_diag_acc)


w_run.on_click(render)
if url_params:
    render()
'''

_DISPLAY = '''\
# --- Display the app ---
_date_row = (widgets.HBox([w_from, w_to],
                          layout=widgets.Layout(margin='2px 0'))
             if w_from is not None else widgets.HTML(''))
display(widgets.VBox([ctrl.box, _date_label, _date_row, w_run, out]))
'''


def build_notebook(dashboard, flavor: str = 'prod') -> nbformat.NotebookNode:
    info = classify_panels(dashboard)
    variables = serialize_variables(dashboard, flavor=flavor)
    method_preamble = _EXP_METHOD_PREAMBLE if flavor == 'exp'   else ""
    before_table    = _FLEET_BEFORE_TABLE  if flavor == 'fleet' else ""
    date_pickers    = _DATE_PICKERS_EXP    if flavor == 'exp'   else _DATE_PICKERS_NONE
    nb = new_notebook()
    intro = info["intro"].strip()
    # Strip a leading "# Title" line from the intro — the Grafana text panel
    # often embeds its own h1 that duplicates the dashboard title.
    intro_lines = intro.splitlines()
    if intro_lines and intro_lines[0].startswith('# '):
        intro_lines = intro_lines[1:]
        while intro_lines and not intro_lines[0].strip():
            intro_lines = intro_lines[1:]
        intro = '\n'.join(intro_lines)
    # Always show exactly one title line.
    header = f"# {dashboard.title}" + (f"\n\n{intro}" if intro else "")
    nb.cells = [
        new_markdown_cell(header),
        new_code_cell(_SETUP.format(variables_json=_compact_json(variables))),
        new_code_cell(_URL_PARAMS),
        new_code_cell(_CONTROLS.format(date_pickers=date_pickers)),
        new_code_cell(_RENDER.format(
            summary_panels_python=_panels_to_python(info["summary"]),
            metric_layouts_json=_compact_json(_METRIC_LAYOUTS),
            repeat_var=info["repeat_var"],
            method_preamble=method_preamble,
            before_table=before_table,
        )),
        new_code_cell(_DISPLAY),
    ]
    nb.metadata.update({
        "kernelspec": {"display_name": "Python 3", "language": "python",
                       "name": "python3"},
        "language_info": {"name": "python"},
    })
    # Collapse all code cell inputs by default in JupyterLab.
    # Voilà already hides code; this makes Jupyter behave the same way.
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.metadata.setdefault("jupyter", {})["source_hidden"] = True
    return nb


def write_notebook(dashboard, dashboard_path: str,
                   outdir: str = "notebooks.stage",
                   flavor: str = 'prod') -> Path:
    nb = build_notebook(dashboard, flavor=flavor)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{_slug(dashboard.title)}.ipynb"
    nbformat.write(nb, str(path))
    return path
