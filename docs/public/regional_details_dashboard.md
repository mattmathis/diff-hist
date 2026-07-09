<!-- TODO: First load gets a spurious BQ error -->

# Regional Details Dashboard

Detailed performance distributions for the cross-product of selected client ISPs
and M-Lab servers near an anchor metro. Plot lines that lie together mean uniform
service; lines that spread apart reveal servers that perform better or worse for
some users.
<!-- snip:index -->

The **Anchor Metro** defines the region: it preselects the M-Lab servers within
the chosen **radius** and ranks the client ISPs by traffic in that metro. A
combined PDF + CDF chart is then shown for every selected server crossed with
every selected client ISP, with test counts in the legend. See the paper or
slides for how differences in these plots reveal asymmetric mid-path routing
that can adversely affect users.
<!-- snip:intro -->

## Selectors

- **Anchor Metro** — geographic centre used to preselect servers and rank client
  ISPs. Changing it re-populates both Servers and Client ISPs.
- **Radius (km)** — how far from the anchor to include additional M-Lab servers.
- **Servers** — select M-Lab sites within the radius; defaults to all.
- **Client Rows** — how many top-ranked ISPs to pre-process.
- **Client ISPs** — select a subset of Client ISPs to plot; defaults to **all** selected.
- **Extra rows** — appears when the selected servers span multiple metros. Client
  ISPs are ranked independently per metro, so an ISP's rank can differ between
  them; Extra rows pulls in additional ranked ISPs so cross-metro comparisons
  aren't truncated.
- **Metrics** — which metrics to plot: MinRTT (log and linear), MeanThroughputMbps,
  LossRate.
- **Table** — whether to show the summary-statistics table (`none` / `Summary` /
  `Verbose`), and **Table Field** — which metric it reports.
- **Method** — `cached` (default), `live`, or `live-DS16`. Date pickers appear
  only for the non-cached methods.

## Reading the charts

Each chart row is one client ISP; each chart is one metric, with one line per
M-Lab server site. The figure is split vertically — **PDF** (probability density)
in the bottom half, **CDF** (cumulative distribution) in the top half — sharing
the same x-axis and a consistent color per site. Legend labels include the
total test count.

## Cached data

With `method=cached` the notebook uses pre-computed histograms; the actual data
date range is shown below the selectors after clicking **Run / Refresh**.
