<!-- Design/architecture doc for how public documentation is authored and built.
     Developer-facing (not a tool doc; gen_public_docs.py ignores it). -->

# Public documentation layout

How the user-facing dashboard documentation is authored once and rendered into
three places. Agreed 2026-07-08.

## Principle: one source, three renderings

Each tool's documentation is authored **once** in `docs/public/<slug>.md` (the
*full tool document*, the source of truth). The two shorter renderings — the
**index** blurb and the notebook **first cell** — are *extracted from the top*
of that same file, so there is no duplicated prose to drift.

| Rendering | Content | Where it appears |
|---|---|---|
| **Index blurb** | abridged top of the full doc + "Full documentation →" link | `notebooks/index.ipynb` accordion for that tool |
| **First cell** | intro top of the full doc + templated boilerplate + links | first markdown cell of the tool's notebook |
| **Full document** | the whole `<slug>.md` | the hosted docs page (see Open decisions) |

`index ⊂ first-cell ⊂ full` — the index is the most abridged, the first cell a
little fuller, the full doc complete.

## Marker scheme

Two nested HTML-comment markers in each `<slug>.md` delimit the extracts (they
are invisible when the markdown is rendered):

```markdown
# <Tool Title>

<crisp 1–2 sentence abstract>            ← index blurb (everything above snip:index)
<!-- snip:index -->

<one or two more intro paragraphs>       ← first-cell intro = everything above snip:intro
<!-- snip:intro -->

## <first full-doc heading>
…full detail: selectors, columns, caveats…
```

- **Index blurb** = text between the `# Title` and `<!-- snip:index -->`.
- **First-cell intro** = text between the `# Title` and `<!-- snip:intro -->`
  (this *includes* the index blurb).
- **Full doc** = the entire file with the two marker lines stripped.

Every full doc must contain both markers (in order). A doc with no markers falls
back to: index blurb = first paragraph, first-cell intro = everything before the
first `##` heading (so the build never fails, just degrades).

## Templated first-cell boilerplate

The first cell appends shared boilerplate from `_boilerplate.md`, with
`{tool}`, `{index_url}`, and `{fulldoc_url}` substituted per tool. It carries:

- the **pre-release / experimental** status notice,
- a link **back to the index** ("About the project") for project-wide context,
- a link to the **full documentation**.

The index blurb also ends with a **"Full documentation →"** link. So both short
renderings always point to the full doc, and the first cell also points home to
the index.

## Files in `docs/public/`

| File | Role | Consumed by build? |
|---|---|---|
| `<slug>.md` (6 tool docs) | full tool document (source of truth) | yes — split on markers |
| `_project.md` | project-level intro: the top of the index page **and** the "About the project" link target | yes |
| `_boilerplate.md` | first-cell boilerplate template (`{tool}`/`{index_url}`/`{fulldoc_url}`) | yes |
| `README.md`, `LAYOUT.md` | dev-facing meta docs | no — ignored |

Tool docs, their notebooks, and which index they appear on:

| `<slug>.md` | notebook(s) | index |
|---|---|---|
| `regional_details_dashboard` | `regional_details_dashboard.ipynb` | public |
| `global_metro_bar_chart` | `global_metro_bar_chart.ipynb` | public |
| `m_lab_calibration_dashboard` | `m_lab_calibration_dashboard.ipynb` | public |
| `fleet_and_egress_load` | `fleet_and_egress_load.ipynb` | public (internal tool) |
| `experimental_regional_details` | `experimental_regional_details.ipynb` | public (dev only, last) |
| `competition_reports` | both `internal/differential_competition_report_for_{minrtt,throughput}_*.ipynb` | **internal** |

The four Drive-sourced docs are regional details, bar chart, calibration, and
competition; `fleet_and_egress_load` and `experimental_regional_details` were
harvested from the hand-written index accordions. Competition reports appear on
the **internal** index (`notebooks/internal/index.ipynb`), not the public one.

## Build tool: `tools/gen_public_docs.py`

A **standalone** tool (deliberately *not* part of `notebook_builder.py`, so this
public-docs work on `polish1` edits different files than the backend/converter
work on `NewBackend` — avoiding merge conflicts). It:

1. reads `docs/public/*.md` + `_project.md` + `_boilerplate.md`;
2. splits each tool doc on the markers;
3. **assembles the indexes into `notebooks.stage/`** (never `notebooks/` — same
   safe-output convention as `convert.py`): `notebooks.stage/index.ipynb` from
   `_project.md` + one accordion per public tool, and
   `notebooks.stage/internal/index.ipynb` for the competition reports. The user
   hand-merges from stage into `notebooks/`.
4. **injects the first cell** of each tool notebook (in `notebooks.stage/`):
   first-cell intro + boilerplate + links;
5. **renders the static HTML site** into `docs/public/site/`: one
   `<slug>.html` per tool (the full doc) plus `index.html` (the same content as
   the public `index.ipynb`, with notebook links absolutized to Voilà URLs).
   Links point at `https://annealing.mattmathis.net/differential-histograms/<slug>`.

Every generated artifact carries a `GENERATED from diff-hist/<path>` comment
near the top (HTML comment; invisible when rendered).

Consequences / properties:
- **The index becomes generated** (assembled from `_project.md` + the tool
  blurbs) into `notebooks.stage/index.ipynb`, replacing the hand-maintained
  accordions. Its project intro lives in `_project.md`.
- Notebook first cells are generated too, so they survive `convert.py`
  regeneration (run `gen_public_docs.py` after `convert.py`). Prose is never
  hand-edited in `.ipynb` JSON.
- **Deterministic HTML** — rendered with **mistune** (`table` plugin), fixed
  template, no timestamps/generator tags. Rebuilds are byte-identical, so
  committing `docs/public/site/` produces no churn on unchanged docs.
- **Build HTML locally and commit it**; annealing only serves it (avoids
  mistune-version drift between machines).

## Resolved decisions

- **Full-doc hosting:** the full docs are static HTML in `docs/public/site/`,
  served under `https://annealing.mattmathis.net/differential-histograms/`, one
  page per tool at `…/differential-histograms/<slug>`. This sets `{fulldoc_url}`.
- **`{index_url}`** = the served public index home, `https://annealing.mattmathis.net/`.
- **Generated indexes go to `notebooks.stage/`**, hand-merged into `notebooks/`.
- **`docs/public/site/` is committed** (deterministic build; deploy is plain
  `git pull` on annealing, no build step there).

### nginx serving (add on annealing, alongside the Voilà proxy)

```nginx
location /differential-histograms/ {
    alias /home/mattmathis/Projects/diff-hist/docs/public/site/;
    try_files $uri $uri.html $uri/index.html =404;   # extensionless URLs → <slug>.html
}
```

## Status

Done: `_project.md`, `_boilerplate.md`, the six tool docs (markers + harvested
index content), `tools/gen_public_docs.py` (first-cell injection, `notebooks.stage/`
indexes, deterministic `docs/public/site/` HTML with `GENERATED` comments).
**Remaining:** add the nginx `location` above on annealing; then the usual
review → hand-merge staged notebooks into `notebooks/` → commit → deploy.
