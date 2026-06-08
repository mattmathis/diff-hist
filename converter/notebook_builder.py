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
_CACHED_FIELD_OPTIONS = {'none', 'MinRTT', 'MeanThroughputMbps', 'LossRate'}


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
            v['options'] = [o for o in v['options']
                            if o['value'] in _CACHED_FIELD_OPTIONS]
            cur = (v.get('current') or {}).get('value')
            if cur not in _CACHED_FIELD_OPTIONS:
                v['current'] = {'value': 'none'}
        out.append(v)
    return out


def serialize_variables(dashboard) -> list[dict]:
    """Bake dashboard variable metadata into a plain list of dicts.

    All fields needed by :class:`Controls` and :func:`default_values` are
    preserved; runtime-only fields (``refs``) are dropped. Options that are
    unsupported for the cached histogram path are pruned by
    :func:`_filter_for_cached`.
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
    return _filter_for_cached(out)


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
from datetime import datetime, date, time, timedelta

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

_CONTROLS = '''\
# --- Dashboard controls (dropdowns; query-backed ones are chained) ---
ctrl = Controls(VARIABLES, client, presets=url_params)
w_run = widgets.Button(description="Run / Refresh", button_style="primary", icon="play")
'''

_RENDER = '''\
# --- Panels (converted from the dashboard) ---
SUMMARY_PANELS  = json.loads(r"""{summary_json}""")
REPEAT_PANELS   = json.loads(r"""{repeat_json}""")
REPEAT_VAR      = {repeat_var!r}
DIAGNOSTIC_TITLE = {diagnostic_title!r}

out = widgets.Output()


def _diagnostics(ctx, from_dt, to_dt):
    rows = [("from", from_dt.date().isoformat()), ("to", to_dt.date().isoformat())]
    for k, v in ctx.items():
        rows.append((k, ", ".join(v) if isinstance(v, list) else str(v)))
    return pd.DataFrame(rows, columns=["variable", "value"])


def render(_=None):
    ctx = ctrl.context()
    to_dt = datetime.combine(date.today(), time())
    from_dt = to_dt - timedelta(days=7)
    cache = {{}}

    def query(sql):
        if sql not in cache:
            cache[sql] = rt.run_query(client, sql)
        return cache[sql]

    out.clear_output(wait=True)
    with out:
        display(Markdown(f"### {{DIAGNOSTIC_TITLE}}"))
        display(_diagnostics(ctx, from_dt, to_dt))

        for p in SUMMARY_PANELS:
            display(Markdown("### " + qb.interpolate(p["title"], ctx)))
            sql = qb.interpolate(p["sql"], ctx, from_dt=from_dt, to_dt=to_dt)
            try:
                display(query(sql))
            except Exception as exc:
                display(HTML(f"<pre>query failed: {{exc}}</pre>"))

        repeats = ctx.get(REPEAT_VAR) or []
        if isinstance(repeats, str):
            repeats = [repeats]

        # One BQ query per metric covers all selected client ISPs.
        # The WHERE regex matches by ASN (leading digits + space) — stable
        # across name-text changes.  Python then filters per ISP for each plot.
        bulk = {{}}
        bulk_ctx = dict(ctx)
        bulk_ctx[REPEAT_VAR] = qb.Raw(rt.asn_regex(repeats))
        for p in REPEAT_PANELS:
            if p.get("skip_if_field_none") and ctx.get("field") == "none":
                continue
            sql_tmpl = rt.rewrite_histogram_call(p["sql"], ctx.get("method", "cached"))
            sql_tmpl = rt.make_combined_sql(rt.make_bulk_sql(sql_tmpl))
            sql = qb.interpolate(sql_tmpl, bulk_ctx,
                                 from_dt=from_dt, to_dt=to_dt)
            try:
                bulk[p["id"]] = query(sql)
            except Exception as exc:
                bulk[p["id"]] = exc

        for value in repeats:
            asn = str(value).split()[0]
            display(HTML(f"<h3>{{REPEAT_VAR}}: {{value}}</h3>"))
            figs = []
            for p in REPEAT_PANELS:
                if p.get("skip_if_field_none") and ctx.get("field") == "none":
                    continue
                title = qb.interpolate(p["title"], dict(ctx, **{{REPEAT_VAR: value}}))
                df_all = bulk.get(p["id"])
                if isinstance(df_all, Exception):
                    figs.append(widgets.HTML(f"<b>{{title}}</b><pre>{{df_all}}</pre>"))
                    continue
                df = df_all[df_all["ISPname"].str.startswith(asn + " ")]
                try:
                    fig = rt.plotly_combined_figure(df, p["layout"], title=title)
                    figs.append(go.FigureWidget(fig))
                except Exception as exc:
                    figs.append(widgets.HTML(f"<b>{{title}}</b><pre>{{exc}}</pre>"))
            if figs:
                display(widgets.HBox(figs, layout=widgets.Layout(flex_flow="row wrap")))


w_run.on_click(render)
'''

_DISPLAY = '''\
# --- Display the app ---
display(widgets.VBox([ctrl.box, w_run, out]))
'''


def build_notebook(dashboard) -> nbformat.NotebookNode:
    info = classify_panels(dashboard)
    variables = serialize_variables(dashboard)
    nb = new_notebook()
    intro = info["intro"].strip()
    header = f"# {dashboard.title}\n\n" + (intro if intro else "")
    nb.cells = [
        new_markdown_cell(header),
        new_code_cell(_SETUP.format(variables_json=_compact_json(variables))),
        new_code_cell(_URL_PARAMS),
        new_code_cell(_CONTROLS),
        new_code_cell(_RENDER.format(
            summary_json=_compact_json(info["summary"]),
            repeat_json=_compact_json(info["repeat_panels"]),
            repeat_var=info["repeat_var"],
            diagnostic_title=info["diagnostic_title"],
        )),
        new_code_cell(_DISPLAY),
    ]
    nb.metadata.update({
        "kernelspec": {"display_name": "Python 3", "language": "python",
                       "name": "python3"},
        "language_info": {"name": "python"},
    })
    return nb


def write_notebook(dashboard, dashboard_path: str,
                   outdir: str = "notebooks.stage") -> Path:
    nb = build_notebook(dashboard)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{_slug(dashboard.title)}.ipynb"
    nbformat.write(nb, str(path))
    return path
