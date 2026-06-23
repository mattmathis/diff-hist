#!/usr/bin/env python3
"""Generate docs/dependencies.md — a map of every notebook's BQ dependencies.

Dependency sources
------------------
1. **SQL-level** — backtick-quoted BQ resources found in panel ``rawSql`` and
   variable ``query.rawSql`` strings, extracted by :mod:`converter.parser`.
2. **Python-dispatch** — resources called at runtime by ``runtime.fetch_histograms``
   based on the ``method`` variable.  These are not visible in the dashboard SQL
   (the dashboard only calls the now-removed BQ wrapper), so they are recorded
   here as a static mapping and must be updated if the dispatch logic changes.

Run manually or via ``tools/convert.py`` (called after every conversion):
    python tools/gen_deps.py

Output: ``docs/dependencies.md``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.parser import parse_dashboard  # noqa: E402

DASHBOARDS_DIR = Path("dashboards")
OUTPUT         = Path("docs/dependencies.md")

# BQ resources called by runtime.fetch_histograms at runtime (not in SQL).
# Update this table whenever the dispatch logic in runtime.py changes.
_FETCH_HISTOGRAMS_DISPATCH = [
    ("access_ndt7_cached_histograms",        "cached",              "method contains 'cached'"),
    ("experimental_ndt7_isp_histograms",     "exp / DS16 / DS1C",   "method contains 'exp' or 'DS'"),
    ("unified_ndt7_isp_histograms",          "live (no backend yet)", "all other methods"),
]

# Flavors that route histogram data through fetch_histograms.
_USES_FETCH_HISTOGRAMS = {"prod", "exp"}

# Human-readable role hints keyed by resource name fragment.
_ROLE_HINTS: dict[str, str] = {
    "regional_report":               "metro-level summary statistics (summary table)",
    "access_ndt7_cached_histograms": "pre-computed histogram cache (PDF/CDF plots)",
    "access_ndt7_isp_histograms":    "histogram wrapper — bypassed at runtime by fetch_histograms",
    "access_exp_ndt7_isp_histograms":"histogram wrapper — bypassed at runtime by fetch_histograms",
    "experimental_ndt7_isp_histograms": "live experimental histogram data (PDF/CDF plots)",
    "unified_ndt7_isp_histograms":   "live unified histogram data (PDF/CDF plots, no backend yet)",
    "cached_metadata":               "server/site geo metadata (anchor & server dropdowns)",
    "global_fleet_inventory":        "fleet egress inventory (table + map)",
    "cached_metro_report":           "metro-level KS distance and spread (bar charts + dropdown)",
}


def _role(name: str) -> str:
    for fragment, hint in _ROLE_HINTS.items():
        if fragment in name:
            return hint
    return ""


def _detect_flavor(dashboard) -> str:
    """Detect notebook flavor without importing tools.convert (avoids circular)."""
    var_names   = {v.name for v in dashboard.variables}
    panel_types = {p.type for p in dashboard.panels}
    if "methodsrc" in var_names:
        return "exp"
    if "method" in var_names:
        return "prod"
    if "barchart" in panel_types:
        return "barchart"
    return "fleet"


def gen_all(dashboards_dir: Path = DASHBOARDS_DIR,
            output: Path = OUTPUT) -> None:
    """Scan all dashboards and write ``docs/dependencies.md``."""
    dashboards_dir = Path(dashboards_dir)
    output = Path(output)

    rows: list[dict] = []       # one entry per (notebook, resource) pair
    per_nb: dict[str, dict] = {}  # notebook slug → metadata + resource list

    for json_path in sorted(dashboards_dir.glob("*.json")):
        dash   = parse_dashboard(json_path)
        flavor = _detect_flavor(dash)
        slug   = _slug(dash.title)

        refs = dash.unique_resources(include_commented=False)

        nb_entry = {
            "title":   dash.title,
            "slug":    slug,
            "flavor":  flavor,
            "file":    json_path.name,
            "resources": [],
        }

        for name, ref in sorted(refs.items()):
            kind = "table function" if ref.is_function else "table"
            role = _role(name)
            nb_entry["resources"].append({
                "name": name, "kind": kind, "path": ref.qualified,
                "access": "SQL", "role": role,
            })
            rows.append({
                "notebook": slug, "resource": name, "kind": kind,
                "access": "SQL", "role": role,
            })

        # Python-dispatch resources (not in SQL)
        if flavor in _USES_FETCH_HISTOGRAMS:
            for res_name, method_flag, condition in _FETCH_HISTOGRAMS_DISPATCH:
                role = _role(res_name)
                nb_entry["resources"].append({
                    "name": res_name, "kind": "table function",
                    "path": f"mm_preproduction.{res_name}",
                    "access": f"Python fetch_histograms ({method_flag})",
                    "role": role,
                })
                rows.append({
                    "notebook": slug, "resource": res_name, "kind": "table function",
                    "access": f"Python fetch_histograms ({method_flag})",
                    "role": role,
                })

        per_nb[slug] = nb_entry

    # --- render markdown ---
    lines = [
        "# BigQuery ↔ Notebook Dependencies",
        "",
        "> Auto-generated by `tools/gen_deps.py`.",
        "> Refreshed automatically on every `tools/convert.py` run.",
        "> Update `_FETCH_HISTOGRAMS_DISPATCH` in this file if the dispatch",
        "> logic in `runtime.fetch_histograms` changes.",
        "",
        "## Summary",
        "",
        "| Notebook | BQ resource | Access | Role |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['notebook']}` | `{r['resource']}` "
            f"| {r['access']} | {r['role']} |"
        )

    lines += ["", "## Per-notebook detail", ""]
    for slug, nb in per_nb.items():
        lines += [
            f"### `{slug}` ({nb['flavor']})",
            "",
            f"Dashboard: `{nb['file']}`",
            "",
            "| BQ resource | Kind | Access path | Role |",
            "|---|---|---|---|",
        ]
        for res in nb["resources"]:
            lines.append(
                f"| `{res['name']}` | {res['kind']} "
                f"| {res['access']} | {res['role']} |"
            )
        lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")
    print(f"Wrote {output}  ({len(rows)} dependency rows, {len(per_nb)} notebooks)")


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()


if __name__ == "__main__":
    gen_all()
