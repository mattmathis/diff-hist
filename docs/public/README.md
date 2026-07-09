# Public dashboard documentation

User-facing documentation for the Differential Histogram dashboards — one
Markdown file per dashboard. Distinct from the **developer** docs
(`docs/functions/`, `docs/dependencies.md`, `backend/`, the `CLAUDE.md` files).

**Source of truth:** edit these `.md` files. They are meant to be surfaced to
users in two places (wiring is a planned follow-up, not yet built):

- the index landing page accordions (`notebooks/index.ipynb`), and
- each dashboard notebook's intro, injected by the converter at build time
  (so in-context help can't drift from this source).

| File | Dashboard notebook |
|---|---|
| `regional_details_dashboard.md` | `regional_details_dashboard.ipynb` |
| `global_metro_bar_chart.md` | `global_metro_bar_chart.ipynb` |
| `m_lab_calibration_dashboard.md` | `m_lab_calibration_dashboard.ipynb` |
| `competition_reports.md` | `internal/differential_competition_report_for_{minrtt,throughput}_*.ipynb` |

**Provenance & status:** converted from the M-Lab Google Drive docs that were
written for the original **Grafana** dashboards, then lightly revised for the
current Voilà/Jupyter notebooks. They still need a careful review pass — each
file has an HTML `REVIEW` comment at the top noting what was changed and what to
verify.

The obsolete "Metro Details" Drive doc was intentionally **not** imported; parts
may be reconstructed with the v3 metro-summary work.
