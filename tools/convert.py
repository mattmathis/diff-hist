#!/usr/bin/env python3
"""Convert a Grafana dashboard JSON into a Jupyter notebook.

Output is written to ``notebooks.stage/`` (rerun-safe). Merge into ``notebooks/``
by hand.

Flavor is auto-detected: dashboards that contain a ``methodsrc`` variable are
treated as ``exp``; everything else as ``prod``.  Override with ``--flavor``.

Usage:
    python tools/convert.py "dashboards/Regional Details Dashboard-1780534101638.json"
    python tools/convert.py "dashboards/Experimental Regional Details Dashboard.json"
    python tools/convert.py <dashboard.json> --outdir notebooks.stage --flavor exp
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.notebook_builder import write_notebook  # noqa: E402
from converter.parser import parse_dashboard           # noqa: E402
from gen_deps import gen_all as _gen_deps              # noqa: E402


def _detect_flavor(dashboard) -> str:
    var_names  = {v.name for v in dashboard.variables}
    panel_types = {p.type for p in dashboard.panels}
    if 'methodsrc' in var_names:
        return 'exp'
    if 'method' in var_names:
        return 'prod'
    if 'barchart' in panel_types:
        return 'barchart'
    return 'fleet'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dashboard", help="path to a Grafana dashboard JSON")
    ap.add_argument("--outdir", default="notebooks.stage")
    ap.add_argument("--flavor", choices=["prod", "exp"], default=None,
                    help="override auto-detected flavor")
    args = ap.parse_args()

    dash = parse_dashboard(args.dashboard)
    flavor = args.flavor or _detect_flavor(dash)
    path = write_notebook(dash, args.dashboard, outdir=args.outdir, flavor=flavor)
    print(f"Wrote {path}  ({len(dash.panels)} panels, "
          f"{len(dash.variables)} variables, flavor={flavor})")
    _gen_deps()   # keep docs/dependencies.md current
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
