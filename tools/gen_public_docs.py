#!/usr/bin/env python3
"""Generate public documentation surfaces from docs/public/*.md.

One source of truth per tool (`docs/public/<slug>.md`, the full document); the
index blurb and the notebook first cell are extracted from the top via the
`<!-- snip:index -->` / `<!-- snip:intro -->` markers (see docs/public/LAYOUT.md).

Outputs (deterministic — no timestamps/versions, so rebuilds are byte-identical):

* notebooks.stage/index.ipynb            — public landing page
* notebooks.stage/internal/index.ipynb   — internal (competition) landing page
* first markdown cell of each staged tool notebook (intro + boilerplate + links)
* docs/public/site/<slug>.html           — full doc per tool (served static HTML)
* docs/public/site/index.html            — same content as the public index

Every generated artifact carries a `GENERATED from diff-hist/<path>` comment.

Run AFTER convert.py has populated notebooks.stage/. Standalone (needs nbformat
and mistune, both already present via jupyter) so it stays independent of
converter/notebook_builder.py. Build the HTML **locally** and commit it; annealing
just serves it (avoids mistune-version drift). Rendered with mistune 3.x.
"""
from __future__ import annotations

import re
from pathlib import Path

import mistune
import nbformat
from nbformat.v4 import new_markdown_cell, new_notebook

DOCS = Path("docs/public")
SITE = DOCS / "site"
STAGE = Path("notebooks.stage")
FULLDOC_BASE = "https://annealing.mattmathis.net/differential-histograms"
# The polished HTML index is the canonical landing page; "project overview"
# links point there rather than at the .ipynb index (kept but being deprecated).
INDEX_URL = "https://annealing.mattmathis.net/differential-histograms/"
VOILA_BASE = "https://annealing.mattmathis.net/voila/render/"

# Ordered index entries. `doc` -> docs/public/<doc>.md; `nb` -> staged notebook
# to inject; `link` -> index link target (relative, may carry query args).
ENTRIES = [
    dict(doc="global_metro_bar_chart", title="Global Metro Bar Chart",
         nb="global_metro_bar_chart.ipynb",
         link="global_metro_bar_chart.ipynb?renderNow=True", index="public"),
    dict(doc="regional_details_dashboard", title="Regional Details Dashboard",
         nb="regional_details_dashboard.ipynb",
         link="regional_details_dashboard.ipynb", index="public"),
    dict(doc="fleet_and_egress_load", title="Fleet and Egress Load",
         nb="fleet_and_egress_load.ipynb",
         link="fleet_and_egress_load.ipynb", index="public"),
    dict(doc="m_lab_calibration_dashboard", title="M-Lab Calibration Dashboard",
         nb="m_lab_calibration_dashboard.ipynb",
         link="m_lab_calibration_dashboard.ipynb", index="public"),
    dict(doc="experimental_regional_details", title="Experimental Regional Details",
         nb="experimental_regional_details.ipynb",
         link="experimental_regional_details.ipynb", index="public"),
    dict(doc="competition_reports", title="Competition Report — minRTT",
         nb="internal/differential_competition_report_for_minrtt_internal_use_only.ipynb",
         link="differential_competition_report_for_minrtt_internal_use_only.ipynb",
         index="internal"),
    dict(doc="competition_reports", title="Competition Report — Throughput",
         nb="internal/differential_competition_report_for_throughput_internal_use_only.ipynb",
         link="differential_competition_report_for_throughput_internal_use_only.ipynb",
         index="internal"),
]

_INTERNAL_HEADER = (
    "# Differential Competition Reports\n\n"
    "**Internal use only — do not share.** These reports identify nearby M-Lab "
    "servers that outperform a given server to some client networks."
)

_NB_META = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}

_CSS = ("body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "line-height:1.55;max-width:52rem;margin:2rem auto;padding:0 1rem;color:#222}"
        "h1,h2,h3{line-height:1.25}table{border-collapse:collapse;margin:1rem 0}"
        "th,td{border:1px solid #ccc;padding:4px 9px;text-align:left;vertical-align:top}"
        "code{background:#f3f3f3;padding:1px 4px;border-radius:3px}"
        "blockquote{border-left:4px solid #ddd;margin:1rem 0;padding:.2rem 1rem;color:#555}"
        "a{color:#1a5fb4}")

_MD = mistune.create_markdown(plugins=["table"])


def gen_md(src: str) -> str:
    return f"<!-- GENERATED from diff-hist/{src} by tools/gen_public_docs.py — edit the source, not here. -->"


def _strip_leading_comments(text: str) -> str:
    while True:
        m = re.match(r"\s*<!--.*?-->\s*", text, flags=re.S)
        if not m:
            return text
        text = text[m.end():]


def _drop_comments(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<!--.*?-->", "", text, flags=re.S)).strip()


def parse_doc(path: Path) -> dict:
    """Return {title, index_blurb, intro, full} for a tool doc."""
    text = _strip_leading_comments(path.read_text())
    m = re.search(r"^#\s+(.+)$", text, flags=re.M)
    title = m.group(1).strip() if m else path.stem
    body = text[m.end():] if m else text

    def upto(marker: str):
        i = body.find(marker)
        return body[:i] if i != -1 else None

    index_blurb = upto("<!-- snip:index -->")
    intro = upto("<!-- snip:intro -->")
    if index_blurb is None:
        index_blurb = body.strip().split("\n\n", 1)[0]
    if intro is None:
        j = body.find("\n## ")
        intro = body[:j] if j != -1 else body
    return {
        "title": title,
        "index_blurb": _drop_comments(index_blurb),
        "intro": _drop_comments(intro),
        "full": _drop_comments(text),   # whole doc, comments + snip markers gone
    }


def fulldoc_url(doc_slug: str) -> str:
    return f"{FULLDOC_BASE}/{doc_slug}"


def _absolutize_nb_links(md: str) -> str:
    """Rewrite relative `foo.ipynb…` links to absolute Voilà URLs (for HTML)."""
    return re.sub(r"\]\((?!https?://)([^)\s]+\.ipynb[^)\s]*)\)",
                  lambda m: f"]({VOILA_BASE}{m.group(1)})", md)


def render_html(title: str, body_md: str, src: str) -> str:
    body = _MD(body_md)
    return (
        "<!doctype html>\n"
        f"<!-- GENERATED from diff-hist/{src} by tools/gen_public_docs.py — do not edit. -->\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_CSS}</style>\n</head>\n"
        f"<body>\n<main>\n{body}</main>\n</body>\n</html>\n"
    )


def main() -> int:
    boilerplate = _strip_leading_comments((DOCS / "_boilerplate.md").read_text()).strip()
    project_md = _strip_leading_comments((DOCS / "_project.md").read_text()).strip()
    project_title = (re.search(r"^#\s+(.+)$", project_md, flags=re.M)
                     or re.match(r"(.*)", "Documentation")).group(1).strip()

    parsed = {e["doc"]: parse_doc(DOCS / f"{e['doc']}.md") for e in ENTRIES}

    # --- first-cell injection into staged notebooks ---
    injected, skipped = [], []
    for e in ENTRIES:
        nbpath = STAGE / e["nb"]
        if not nbpath.exists():
            skipped.append(e["nb"]); continue
        d = parsed[e["doc"]]
        src = f"docs/public/{e['doc']}.md"
        cell = (gen_md(src) + "\n\n"
                + f"# {d['title']}\n\n{d['intro']}\n\n"
                + boilerplate.format(tool=d["title"], index_url=INDEX_URL,
                                     fulldoc_url=fulldoc_url(e["doc"])))
        nb = nbformat.read(str(nbpath), as_version=4)
        if nb.cells and nb.cells[0].cell_type == "markdown":
            nb.cells[0]["source"] = cell
        else:
            nb.cells.insert(0, new_markdown_cell(cell))
        nbformat.write(nb, str(nbpath)); injected.append(e["nb"])

    # --- index notebooks (stage) ---
    def section(e, absolutize=False) -> str:
        d = parsed[e["doc"]]
        s = (f"## [{e['title']}]({e['link']})\n\n{d['index_blurb']}\n\n"
             f"[Full documentation →]({fulldoc_url(e['doc'])})")
        return _absolutize_nb_links(s) if absolutize else s

    pub_entries = [e for e in ENTRIES if e["index"] == "public"]
    int_entries = [e for e in ENTRIES if e["index"] == "internal"]

    pub_cells = [new_markdown_cell(gen_md("docs/public/_project.md (+ per-tool docs)")
                                   + "\n\n" + project_md)]
    pub_cells += [new_markdown_cell(section(e)) for e in pub_entries]
    nb = new_notebook(cells=pub_cells); nb.metadata.update(_NB_META)
    STAGE.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(STAGE / "index.ipynb"))

    int_cells = [new_markdown_cell(gen_md("tools/gen_public_docs.py") + "\n\n" + _INTERNAL_HEADER)]
    int_cells += [new_markdown_cell(section(e)) for e in int_entries]
    nb = new_notebook(cells=int_cells); nb.metadata.update(_NB_META)
    (STAGE / "internal").mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(STAGE / "internal" / "index.ipynb"))

    # --- static HTML site ---
    SITE.mkdir(parents=True, exist_ok=True)
    for slug in sorted({e["doc"] for e in ENTRIES}):
        d = parsed[slug]
        (SITE / f"{slug}.html").write_text(
            render_html(d["title"], d["full"], f"docs/public/{slug}.md"))
    # index.html == the public index content, with notebook links absolutized
    index_md = project_md + "\n\n" + "\n\n".join(section(e, absolutize=True)
                                                 for e in pub_entries)
    (SITE / "index.html").write_text(
        render_html(project_title, index_md, "docs/public/_project.md (+ per-tool docs)"))

    print(f"first-cell injected: {len(injected)}")
    if skipped:
        print(f"skipped (not in {STAGE}/): {', '.join(skipped)}")
    print(f"wrote {STAGE}/index.ipynb, {STAGE}/internal/index.ipynb")
    print(f"wrote {SITE}/ : index.html + {len({e['doc'] for e in ENTRIES})} tool pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
