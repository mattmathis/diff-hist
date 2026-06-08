#!/usr/bin/env python3
"""Convert a Grafana dashboard JSON into a Jupyter notebook.

Output is written to ``notebooks.stage/`` (rerun-safe). Merge into ``notebooks/``
by hand.

Usage:
    python tools/convert.py "dashboards/Regional Details Dashboard-1780534101638.json"
    python tools/convert.py <dashboard.json> --outdir notebooks.stage
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.notebook_builder import write_notebook  # noqa: E402
from converter.parser import parse_dashboard  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dashboard", help="path to a Grafana dashboard JSON")
    ap.add_argument("--outdir", default="notebooks.stage",
                    help="output directory (default: notebooks.stage)")
    args = ap.parse_args()

    dash = parse_dashboard(args.dashboard)
    path = write_notebook(dash, args.dashboard, outdir=args.outdir)
    print(f"Wrote {path}  ({len(dash.panels)} panels, "
          f"{len(dash.variables)} variables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
