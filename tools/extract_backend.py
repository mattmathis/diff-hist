#!/usr/bin/env python3
"""Extract the BigQuery "fleet" (routines/views/tables) into backend/ as
redeployable SQL — the sql-fleet "extract the baseline" step.

Starting from the notebook-facing routines (ENTRY), walk the transitive
dependency closure *within the dataset* by scanning each object's DDL for
references to other objects in the same dataset. This captures the whole
differential-performance fleet (unified/experimental histograms, get_ndt7_data,
the extended_intermediate_* family, cached tables, the report TVFs) while
excluding unrelated historical/exploratory objects in the dataset.

Authoritative source: `INFORMATION_SCHEMA.ROUTINES.ddl` and
`INFORMATION_SCHEMA.TABLES.ddl` — the exact CREATE statements BigQuery stores,
so the extracted .sql is verbatim and redeployable.

Requires an authenticated `bq` (run where creds exist, e.g. the annealing SA).

    python tools/extract_backend.py --out backend

Writes backend/{routines,views,tables,external}/<name>.sql plus
backend/MANIFEST.json (types, dependency edges, suggested deploy order).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

PROJECT = "mlab-collaboration"
DATASET = "mm_preproduction"

# Notebook-facing routines (from converter/runtime.py + dashboard SQL). The
# closure walk pulls in everything these transitively reference in-dataset.
ENTRY = [
    "access_ndt7_cached_histograms",
    "access_exp_ndt7_isp_histograms",
    "access_ndt7_isp_histograms",
    "experimental_ndt7_isp_histograms",
    "unified_ndt7_isp_histograms",
    "regional_report",
    "calibration_report",
    "minRTT_competition_report",
    "throughput_competition_report",
    "global_fleet_inventory",
    "cached_metro_report",
    "cached_metadata",
    "server_metadata",
]

# Scheduled queries (transfer configs) to extract, matched by displayName
# substring. Their SQL is saved verbatim and also seeds the dependency closure
# (so the objects they read/write are extracted too).
SCHEDULED = [
    "cached_ndt7_isp_histograms",   # weekly "Create cached_ndt7_isp_histograms"
]

_SUBDIR = {  # INFORMATION_SCHEMA object type -> backend/ subdir
    "TABLE FUNCTION": "routines",
    "SCALAR FUNCTION": "routines",
    "PROCEDURE": "routines",
    "VIEW": "views",
    "MATERIALIZED VIEW": "views",
    "BASE TABLE": "tables",
    "EXTERNAL": "external",
}


def _bq_json(sql: str) -> list[dict]:
    """Run a query and return rows as dicts (bq --format=json)."""
    out = subprocess.run(
        ["bq", "query", "--use_legacy_sql=false", "--format=json",
         "--max_rows=100000", sql],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit(f"bq query failed:\n{out.stderr}")
    return json.loads(out.stdout or "[]")


def _slug(name: str) -> str:
    s = re.sub(r"(?i)^(create|update)\s+", "", name.strip())
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s or "scheduled_query"


def fetch_scheduled() -> list[dict]:
    """Fetch scheduled queries whose displayName matches SCHEDULED."""
    out = subprocess.run(
        ["bq", "ls", "--transfer_config", "--transfer_location=US",
         "--format=json", "--max_results=1000"],
        capture_output=True, text=True)
    if out.returncode != 0:
        print(f"  WARNING: cannot list transfer configs: {out.stderr.strip()[:160]}")
        return []
    picked = []
    for c in json.loads(out.stdout or "[]"):
        dn = c.get("displayName", "")
        if c.get("dataSourceId") != "scheduled_query":
            continue
        if not any(p.lower() in dn.lower() for p in SCHEDULED):
            continue
        show = subprocess.run(
            ["bq", "show", "--transfer_config", "--format=json", c["name"]],
            capture_output=True, text=True)
        if show.returncode != 0:
            print(f"  WARNING: cannot show {dn}: {show.stderr.strip()[:120]}")
            continue
        d = json.loads(show.stdout)
        picked.append({
            "slug": _slug(dn), "displayName": dn, "name": c["name"],
            "schedule": c.get("schedule", ""),
            "query": (d.get("params") or {}).get("query", ""),
        })
    return picked


def fetch_inventory() -> dict[str, dict]:
    """Return {name: {type, ddl}} for every routine, table and view."""
    inv: dict[str, dict] = {}
    routines = _bq_json(
        f"SELECT routine_name AS name, routine_type AS type, ddl "
        f"FROM `{PROJECT}.{DATASET}`.INFORMATION_SCHEMA.ROUTINES")
    for r in routines:
        inv[r["name"]] = {"type": r["type"], "ddl": r["ddl"] or ""}
    tables = _bq_json(
        f"SELECT table_name AS name, table_type AS type, ddl "
        f"FROM `{PROJECT}.{DATASET}`.INFORMATION_SCHEMA.TABLES")
    for t in tables:
        # routine/table names don't collide in practice; tables win only if new
        inv.setdefault(t["name"], {"type": t["type"], "ddl": t["ddl"] or ""})
    return inv


def refs_in(ddl: str, names: set[str], self_name: str) -> set[str]:
    """Which known object names does this DDL reference (whole-word, minus self)?

    Heuristic: a plain whole-word scan of the DDL text. It therefore (a) counts
    names that appear only in `#`/`--` comments or in string literals such as
    ERROR('unified…: …') — which manifests as spurious edges / an apparent
    experimental<->unified cycle — and (b) misses names built dynamically or via
    wildcard tables (e.g. access_ndt7_cached_histograms reads
    `cached_ndt7_isp_histogram*`). The extracted SQL is exact; the edge graph is
    an approximate map. TODO: strip comments/strings before matching.
    """
    found = set()
    for n in names:
        if n == self_name:
            continue
        if re.search(rf"\b{re.escape(n)}\b", ddl):
            found.add(n)
    return found


def closure(inv: dict[str, dict],
            seeds: list[str] | None = None) -> tuple[set[str], dict[str, set[str]]]:
    """BFS from ENTRY (+ seeds); return (reachable names, edges name->deps)."""
    names = set(inv)
    edges: dict[str, set[str]] = {}
    seen: set[str] = set()
    roots = list(ENTRY) + list(seeds or [])
    q = deque(n for n in roots if n in inv)
    missing = [n for n in ENTRY if n not in inv]
    if missing:
        print(f"  WARNING: entry points not found in dataset: {missing}")
    while q:
        name = q.popleft()
        if name in seen:
            continue
        seen.add(name)
        deps = refs_in(inv[name]["ddl"], names, name)
        edges[name] = deps
        for d in deps:
            if d not in seen:
                q.append(d)
    return seen, edges


def deploy_order(nodes: set[str], edges: dict[str, set[str]],
                 inv: dict[str, dict]) -> list[str]:
    """Topological order so a dependency is deployed before its dependents.
    Tables/external come first (data sources); then views/routines by deps."""
    # edges[name] = deps (things name references). Deploy deps first.
    indeg: dict[str, int] = {n: 0 for n in nodes}
    dependents: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        for d in edges.get(n, ()):  # n depends on d
            if d in nodes:
                dependents[d].append(n)
                indeg[n] += 1
    # Kahn, preferring tables/external first for stable, readable ordering.
    def _rank(n: str) -> int:
        t = inv[n]["type"]
        return 0 if t in ("BASE TABLE", "EXTERNAL") else 1
    ready = sorted((n for n in nodes if indeg[n] == 0), key=lambda n: (_rank(n), n))
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in dependents.get(n, ()):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort(key=lambda n: (_rank(n), n))
    # Any leftover (cycles) appended deterministically.
    order += sorted(n for n in nodes if n not in order)
    return order


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="backend", help="output directory")
    args = ap.parse_args()
    out = Path(args.out)

    print(f"Fetching inventory of {PROJECT}.{DATASET} …")
    inv = fetch_inventory()
    print(f"  {len(inv)} objects in dataset")

    print("Fetching scheduled queries …")
    scheduled = fetch_scheduled()
    print(f"  {len(scheduled)} matched: {[s['slug'] for s in scheduled]}")
    # Seed the closure with objects each scheduled query references.
    seeds: set[str] = set()
    for s in scheduled:
        seeds |= refs_in(s["query"], set(inv), self_name="")

    nodes, edges = closure(inv, seeds=sorted(seeds))
    print(f"  fleet closure: {len(nodes)} objects")

    order = deploy_order(nodes, edges, inv)
    pos = {n: i for i, n in enumerate(order)}

    manifest = {"project": PROJECT, "dataset": DATASET,
                "entry_points": ENTRY, "objects": {}, "deploy_order": order}

    for name in sorted(nodes):
        obj = inv[name]
        sub = _SUBDIR.get(obj["type"], "other")
        d = out / sub
        d.mkdir(parents=True, exist_ok=True)
        ddl = obj["ddl"].rstrip()
        # Ensure the statement is self-contained & re-runnable.
        if not ddl.endswith(";"):
            ddl += "\n;"
        (d / f"{name}.sql").write_text(ddl + "\n")
        manifest["objects"][name] = {
            "type": obj["type"], "path": f"{sub}/{name}.sql",
            "depends_on": sorted(edges.get(name, ())),
            "deploy_rank": pos[name],
        }

    # Scheduled queries (transfer configs): verbatim SQL + schedule header.
    manifest["scheduled_queries"] = {}
    if scheduled:
        d = out / "scheduled"
        d.mkdir(parents=True, exist_ok=True)
        for s in scheduled:
            deps = sorted(refs_in(s["query"], set(inv), self_name="") & nodes)
            header = (f"-- Scheduled query: {s['displayName']}\n"
                      f"-- transferConfig: {s['name']}\n"
                      f"-- schedule: {s['schedule'] or '(manual)'}\n\n")
            (d / f"{s['slug']}.sql").write_text(header + s["query"].rstrip() + "\n")
            manifest["scheduled_queries"][s["slug"]] = {
                "display_name": s["displayName"], "transfer_config": s["name"],
                "schedule": s["schedule"], "path": f"scheduled/{s['slug']}.sql",
                "depends_on": deps,
            }

    out.mkdir(parents=True, exist_ok=True)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")

    by_type: dict[str, int] = defaultdict(int)
    for n in nodes:
        by_type[inv[n]["type"]] += 1
    print("  wrote:", dict(by_type))
    print(f"  -> {out}/ ({len(nodes)} files + MANIFEST.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
