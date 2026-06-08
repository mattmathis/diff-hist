#!/usr/bin/env python3
"""Generate reference documentation for the BigQuery resources a dashboard uses.

The dashboards reference BigQuery table functions and tables that are otherwise
undocumented. This tool reads a dashboard JSON, finds every routine/table it
references, pulls the *authoritative* definition straight from BigQuery
(``bq show``), and writes one Markdown file per resource into ``docs/functions/``
for human review.

For each table function it records:
  * the argument list with BigQuery types (authoritative, from the API),
  * the output column schema (probed live, since TVF return types are not
    exposed by ``bq show``),
  * the full SQL definition body,
  * every call site in the dashboard, with the dashboard's argument expressions
    lined up against the formal parameters.

Usage:
    python tools/gen_docs.py "dashboards/Regional Details Dashboard-1780534101638.json"
    python tools/gen_docs.py <dashboard.json> --dataset mlab-collaboration.mm_preproduction

Requires an authenticated ``bq`` CLI on PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Allow running as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.parser import Dashboard, ResourceRef, parse_dashboard  # noqa: E402

# The dashboards use a `${dataset}` constant variable for the BQ path. Its value
# (mlab-collaboration.mm_preproduction) is the default; override with --dataset.
DEFAULT_DATASET = "mlab-collaboration.mm_preproduction"


def _dataset_var_value(dash: Dashboard) -> str | None:
    for v in dash.variables:
        if v.name == "dataset" and v.type == "constant":
            # constant variables store the value in .query (a string)
            return getattr(v, "query_sql", None) or None
    return None


def resolve_qualified(ref: ResourceRef, dataset: str) -> str:
    """Turn a dashboard-qualified name into a bq-show target.

    ``${dataset}.foo`` -> ``project:dataset.foo``; a fully qualified
    ``proj.dataset.foo`` is passed through (dots before the resource become a
    colon for the project boundary).
    """
    q = ref.qualified.replace("${dataset}", dataset)
    parts = q.split(".")
    if len(parts) == 3:
        return f"{parts[0]}:{parts[1]}.{parts[2]}"
    if len(parts) == 2:  # dataset.resource, project implied by dataset string
        return f"{parts[0]}.{parts[1]}"
    return q


def _bq(args: list[str]) -> str:
    res = subprocess.run(
        ["bq", *args], capture_output=True, text=True, timeout=120
    )
    if res.returncode != 0:
        raise RuntimeError(f"bq {' '.join(args)} failed:\n{res.stderr}")
    return res.stdout


def fetch_routine(target: str) -> dict | None:
    """``bq show --routine`` as a dict, or None if it is not a routine."""
    try:
        out = _bq(["show", "--format=prettyjson", "--routine", target])
    except RuntimeError:
        return None
    return json.loads(out)


def fetch_table(target: str) -> dict | None:
    try:
        out = _bq(["show", "--format=prettyjson", target])
    except RuntimeError:
        return None
    return json.loads(out)


def type_str(dt: dict) -> str:
    """Render a BigQuery dataType dict as a readable type string."""
    kind = dt.get("typeKind", "?")
    if kind == "ARRAY":
        return f"ARRAY<{type_str(dt['arrayElementType'])}>"
    if kind == "STRUCT":
        fields = dt.get("structType", {}).get("fields", [])
        inner = ", ".join(f"{f['name']} {type_str(f['type'])}" for f in fields)
        return f"STRUCT<{inner}>"
    return kind


def _balanced_args(sql: str, open_pos: int) -> str | None:
    """Return the text between ``(`` at ``open_pos`` and its matching ``)``."""
    depth = 0
    for i in range(open_pos, len(sql)):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[open_pos + 1:i]
    return None


def find_call_sites(dash: Dashboard, name: str) -> list[dict]:
    """Locate every SQL call of ``name(...)`` and capture its argument text.

    Handles nested parentheses in argument expressions (e.g.
    ``DATE(REGEXP_EXTRACT(...))``) via balanced-paren scanning.
    """
    sites: list[dict] = []
    # Match the backtick-qualified function token, then find the '(' that opens
    # its argument list (allowing whitespace between the backtick and paren).
    head_re = re.compile(r"`[^`]*\." + re.escape(name) + r"`\s*\(")

    def scan(where: str, sql: str | None):
        if not sql:
            return
        for m in head_re.finditer(sql):
            open_pos = m.end() - 1  # index of the '('
            inner = _balanced_args(sql, open_pos)
            if inner is None:
                continue
            sites.append({"where": where, "args": _split_args(inner)})

    for v in dash.variables:
        scan(f"variable ${{{v.name}}}", v.query_sql)
    for p in dash.panels:
        for t in p.targets:
            scan(f"panel #{p.panel_id} {p.title!r}", t.raw_sql)
    return sites


def _split_args(arg_text: str) -> list[str]:
    """Split a call's argument list on top-level commas."""
    args, depth, buf = [], 0, []
    for ch in arg_text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        args.append("".join(buf).strip())
    return args


def probe_output_schema(target: str, routine: dict, call_sites: list[dict]) -> list[dict] | None:
    """Run the function with one call site's arguments to learn its columns.

    TVF return types are not exposed by the API, so we execute the function with
    ``LIMIT 0`` using the arguments from the first call site, substituting
    representative literals for any ``${...}`` Grafana variables we cannot
    resolve. Returns the result schema (list of {name,type}) or None on failure.
    """
    if not call_sites:
        return None
    args = call_sites[0]["args"]
    params = routine.get("arguments", [])
    if len(args) != len(params):
        return None
    # Build a literal for each parameter based on its declared type.
    literals = []
    for raw, p in zip(args, params):
        lit = _resolve_arg_literal(raw, p.get("dataType", {}).get("typeKind", "STRING"))
        literals.append(lit)
    fq = target.replace(":", ".")
    sql = f"SELECT * FROM `{fq}`({', '.join(literals)}) LIMIT 0"
    try:
        out = _bq([
            "query", "--use_legacy_sql=false", "--format=prettyjson",
            "--max_rows=0", sql,
        ])
    except RuntimeError as e:
        print(f"  (output-schema probe failed: {e})", file=sys.stderr)
        return None
    # With LIMIT 0 the data array is empty; pull schema from a dry-run instead.
    try:
        dry = subprocess.run(
            ["bq", "query", "--use_legacy_sql=false", "--dry_run",
             "--format=prettyjson", sql],
            capture_output=True, text=True, timeout=120,
        )
        meta = json.loads(dry.stdout)
        fields = meta["statistics"]["query"]["schema"]["fields"]
        return [{"name": f["name"], "type": f["type"]} for f in fields]
    except Exception:
        return None


def _resolve_arg_literal(raw: str, type_kind: str) -> str:
    """Replace Grafana ${var} placeholders with a type-appropriate literal."""
    if "${" in raw or raw.startswith("$"):
        if type_kind in ("INT64", "FLOAT64", "NUMERIC"):
            return "5"
        if type_kind == "DATE":
            return "DATE '2025-01-28'"
        return "'.*'"
    # Already a literal (quoted string, number, DATE(...), etc.) — pass through,
    # but neutralise any embedded ${...}.
    return re.sub(r"\$\{[^}]+\}", ".*", raw)


def render_routine_doc(name: str, target: str, routine: dict,
                       output_schema: list[dict] | None,
                       call_sites: list[dict]) -> str:
    args = routine.get("arguments", [])
    lines = [f"# `{name}` (table function)", ""]
    lines.append(f"**BigQuery resource:** `{target}`  ")
    lines.append(f"**Type:** {routine.get('routineType', 'TABLE FUNCTION')}")
    lines.append("")
    lines.append("> Auto-generated from the live BigQuery definition by "
                 "`tools/gen_docs.py`. Review and edit the prose sections; the "
                 "signature, schema, and definition are authoritative.")
    lines.append("")

    lines.append("## Description")
    lines.append("")
    lines.append("_TODO (review): summarise what this function computes._")
    lines.append("")

    lines.append("## Arguments")
    lines.append("")
    if args:
        lines.append("| # | Name | Type |")
        lines.append("|---|------|------|")
        for i, a in enumerate(args, 1):
            lines.append(f"| {i} | `{a.get('name','')}` | {type_str(a.get('dataType', {}))} |")
    else:
        lines.append("_No arguments._")
    lines.append("")

    lines.append("## Output columns")
    lines.append("")
    if output_schema:
        lines.append("| Column | Type |")
        lines.append("|--------|------|")
        for f in output_schema:
            lines.append(f"| `{f['name']}` | {f['type']} |")
    else:
        lines.append("_Could not probe automatically; see the definition's "
                     "final `SELECT` below._")
    lines.append("")

    lines.append("## Call sites in this dashboard")
    lines.append("")
    if call_sites:
        for cs in call_sites:
            lines.append(f"- **{cs['where']}**")
            for i, arg in enumerate(cs["args"]):
                pname = args[i]["name"] if i < len(args) else f"arg{i+1}"
                lines.append(f"    - `{pname}` = `{arg}`")
    else:
        lines.append("_No (active) call sites found._")
    lines.append("")

    lines.append("## Definition (BigQuery DDL)")
    lines.append("")
    lines.append("```sql")
    lines.append(routine.get("definitionBody", "").strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_table_doc(name: str, target: str, table: dict) -> str:
    lines = [f"# `{name}` (table)", ""]
    lines.append(f"**BigQuery resource:** `{target}`  ")
    lines.append(f"**Type:** {table.get('type', 'TABLE')}")
    lines.append("")
    lines.append("> Auto-generated from the live BigQuery schema by "
                 "`tools/gen_docs.py`.")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append("_TODO (review): describe this table and how the dashboard "
                 "uses it._")
    lines.append("")
    lines.append("## Schema")
    lines.append("")
    lines.append("| Column | Type | Mode |")
    lines.append("|--------|------|------|")
    for f in table.get("schema", {}).get("fields", []):
        lines.append(f"| `{f['name']}` | {f['type']} | {f.get('mode','')} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dashboard", help="path to a Grafana dashboard JSON")
    ap.add_argument("--dataset", default=None,
                    help=f"BQ dataset path for ${{dataset}} (default: from "
                         f"dashboard, else {DEFAULT_DATASET})")
    ap.add_argument("--outdir", default="docs/functions")
    ap.add_argument("--include-commented", action="store_true",
                    help="also document resources only referenced in comments")
    args = ap.parse_args()

    dash = parse_dashboard(args.dashboard)
    dataset = args.dataset or _dataset_var_value(dash) or DEFAULT_DATASET
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    resources = dash.unique_resources(include_commented=args.include_commented)
    print(f"Dashboard: {dash.title}")
    print(f"Dataset:   {dataset}")
    print(f"Resources: {', '.join(sorted(resources)) or '(none)'}\n")

    index = [f"# BigQuery resources for: {dash.title}", "",
             f"Dataset: `{dataset}`", ""]

    for ref_name in sorted(resources):
        ref = resources[ref_name]
        target = resolve_qualified(ref, dataset)
        kind = "function" if ref.is_function else "table"
        print(f"- {ref_name} ({kind}) -> {target}")

        if ref.is_function:
            routine = fetch_routine(target)
            if not routine:
                print(f"  !! could not fetch routine {target}", file=sys.stderr)
                continue
            sites = find_call_sites(dash, ref_name)
            schema = probe_output_schema(target, routine, sites)
            doc = render_routine_doc(ref_name, target, routine, schema, sites)
        else:
            table = fetch_table(target)
            if not table:
                print(f"  !! could not fetch table {target}", file=sys.stderr)
                continue
            doc = render_table_doc(ref_name, target, table)

        path = outdir / f"{ref_name}.md"
        path.write_text(doc)
        index.append(f"- [`{ref_name}`]({ref_name}.md) — {kind}")
        print(f"  wrote {path}")

    (outdir / "README.md").write_text("\n".join(index) + "\n")
    print(f"\nIndex: {outdir / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
