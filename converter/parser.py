"""Parse a Grafana dashboard JSON into an internal model.

The dashboards in this project query BigQuery directly (datasource type
``grafana-bigquery-datasource``); there is no PromQL. Each panel target carries
a ``rawSql`` string, and template variables (the dropdowns) may also carry a
``query.rawSql``. Those SQL strings reference BigQuery *table functions* and
*tables*, qualified inside backticks, e.g.::

    FROM `${dataset}.regional_report`(...)
    FROM `mlab-collaboration.mm_preproduction.cached_metadata`

This module extracts:

* dashboard metadata (title, description, uid)
* template variables (the dropdowns + hidden constants)
* panels (with their SQL targets and panel-type-specific config)
* the set of BigQuery routines/tables each SQL string references

The referenced-resource extraction is what feeds the documentation step
(:mod:`tools.gen_docs`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# A backtick-quoted, dotted identifier such as `${dataset}.regional_report` or
# `mlab-collaboration.mm_preproduction.cached_metadata`. Grafana always wraps
# BigQuery resource paths in backticks, which makes them unambiguous to find.
_BACKTICK_IDENT = re.compile(r"`([^`]+)`")


@dataclass
class ResourceRef:
    """A BigQuery resource referenced by a SQL string."""

    name: str  # bare resource name, e.g. "regional_report"
    qualified: str  # as written in SQL, e.g. "${dataset}.regional_report"
    is_function: bool  # True if called as fn(...), False for a plain table
    commented: bool  # True if the reference sits on a commented-out line


@dataclass
class Target:
    """One query target within a panel."""

    ref_id: str
    raw_sql: str
    project: str | None
    refs: list[ResourceRef] = field(default_factory=list)


@dataclass
class Panel:
    panel_id: int
    title: str
    type: str
    description: str = ""
    content: str = ""  # markdown body for text panels
    repeat: str | None = None  # template var this panel/row repeats over
    targets: list[Target] = field(default_factory=list)
    # ae3e-plotly-panel carries a client-side transform script + layout.
    plotly_script: str | None = None
    plotly_layout: dict | None = None


@dataclass
class Variable:
    name: str
    type: str  # query | custom | constant | datasource | textbox | interval
    label: str = ""
    description: str = ""
    hide: int = 0  # 0 visible, 1 hide label, 2 hidden
    multi: bool = False
    regex: str = ""
    options: list[dict] = field(default_factory=list)  # custom/constant options
    current: dict = field(default_factory=dict)
    query_sql: str | None = None  # rawSql for type=query variables
    refs: list[ResourceRef] = field(default_factory=list)


@dataclass
class Dashboard:
    title: str
    uid: str
    description: str
    variables: list[Variable]
    panels: list[Panel]

    def all_refs(self) -> list[ResourceRef]:
        """Every resource reference across variables and panel targets."""
        out: list[ResourceRef] = []
        for v in self.variables:
            out.extend(v.refs)
        for p in self.panels:
            for t in p.targets:
                out.extend(t.refs)
        return out

    def unique_resources(self, include_commented: bool = False) -> dict[str, ResourceRef]:
        """De-duplicated resources keyed by bare name.

        A resource is treated as a function if *any* reference calls it as one.
        Commented-only references are excluded unless ``include_commented``.
        """
        out: dict[str, ResourceRef] = {}
        for r in self.all_refs():
            if r.commented and not include_commented:
                continue
            existing = out.get(r.name)
            if existing is None:
                out[r.name] = ResourceRef(
                    name=r.name,
                    qualified=r.qualified,
                    is_function=r.is_function,
                    commented=r.commented,
                )
            else:
                existing.is_function = existing.is_function or r.is_function
                existing.commented = existing.commented and r.commented
        return out


def _strip_comments(sql: str) -> str:
    """Blank out SQL comments so they don't yield phantom references.

    Replaces ``-- ...`` and ``# ...`` line comments and ``/* ... */`` blocks
    with spaces (preserving length is unnecessary; we only scan the result).
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    out_lines = []
    for line in sql.splitlines():
        line = re.sub(r"(--|#).*$", "", line)
        out_lines.append(line)
    return "\n".join(out_lines)


def extract_refs(raw_sql: str) -> list[ResourceRef]:
    """Find BigQuery resources referenced by a SQL string.

    A backtick identifier immediately followed by ``(`` (ignoring whitespace)
    is a table function call; otherwise it is a plain table. References that
    appear only on commented-out lines are flagged ``commented=True`` so the
    caller can decide whether to document them.
    """
    active = _strip_comments(raw_sql)
    refs: list[ResourceRef] = []
    seen: set[tuple[str, bool]] = set()

    for m in _BACKTICK_IDENT.finditer(raw_sql):
        qualified = m.group(1).strip()
        bare = qualified.split(".")[-1].strip()
        # Skip datasource UID placeholders and obvious non-resources.
        if not bare or bare.startswith("$"):
            continue
        # Wildcard tables (cached_ndt7_isp_histogram*) -> keep the stem.
        bare = bare.rstrip("*")
        # Is the matched span present in the comment-stripped text? If not, it
        # was inside a comment.
        commented = qualified not in active
        # Function if a '(' follows the closing backtick.
        after = raw_sql[m.end():]
        is_function = bool(re.match(r"\s*\(", after))
        key = (bare, is_function)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            ResourceRef(
                name=bare,
                qualified=qualified,
                is_function=is_function,
                commented=commented,
            )
        )
    return refs


def _parse_variable(v: dict) -> Variable:
    query_sql = None
    q = v.get("query")
    if isinstance(q, dict):
        query_sql = q.get("rawSql")
    elif isinstance(q, str):
        # constant/custom variables store a plain string (e.g. the dataset path
        # for type=constant, or a comma list for type=custom).
        query_sql = q
    refs = extract_refs(query_sql) if query_sql and isinstance(q, dict) else []
    return Variable(
        name=v.get("name", ""),
        type=v.get("type", ""),
        label=v.get("label", ""),
        description=v.get("description", ""),
        hide=v.get("hide", 0),
        multi=v.get("multi", False),
        regex=v.get("regex", ""),
        options=v.get("options", []) or [],
        current=v.get("current", {}) or {},
        query_sql=query_sql,
        refs=refs,
    )


def _parse_panel(p: dict) -> Panel:
    targets = []
    for t in p.get("targets", []) or []:
        raw = t.get("rawSql", "")
        targets.append(
            Target(
                ref_id=t.get("refId", ""),
                raw_sql=raw,
                project=t.get("project"),
                refs=extract_refs(raw) if raw else [],
            )
        )
    opts = p.get("options", {}) or {}
    return Panel(
        panel_id=p.get("id", -1),
        title=p.get("title", ""),
        type=p.get("type", ""),
        description=p.get("description", ""),
        content=opts.get("content", "") if p.get("type") == "text" else "",
        repeat=p.get("repeat"),
        targets=targets,
        plotly_script=opts.get("script"),
        plotly_layout=opts.get("layout"),
    )


def parse_dashboard(path: str | Path) -> Dashboard:
    data = json.loads(Path(path).read_text())
    variables = [
        _parse_variable(v) for v in data.get("templating", {}).get("list", [])
    ]
    panels = [_parse_panel(p) for p in data.get("panels", [])]
    return Dashboard(
        title=data.get("title", ""),
        uid=data.get("uid", ""),
        description=data.get("description", ""),
        variables=variables,
        panels=panels,
    )
