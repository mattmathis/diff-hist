<!-- REVIEW: merged from the Drive doc "Documentation for the Global Metro Bar
     Chart" (Grafana-era) and the hand-written index accordion (framing, Controls
     table, Navigation). Revision: navigation described for Voilà (click a bar →
     link appears → click to open Regional Details). Marker tiers below. -->

# Global Metro Bar Chart

Interconnect-quality ("pain") scores for every metro with two or more M-Lab
servers — how differently the top client ISPs are served across a metro's
servers. Lower is better; high values suggest some users may struggle to reach
certain local content.
<!-- snip:index -->

For the top N client (user-access) ISPs in each metro, it finds the worst-case
performance difference between pairs of M-Lab sites serving that ISP. A healthy
interconnection ecosystem produces small differences. MinRTT and throughput are
scored separately: differences in **minRTT** indicate paths longer than
necessary (routing/peering); differences in **throughput** suggest congestion or
a capacity problem between ISPs.
<!-- snip:intro -->

## The two measures

- **Spread (ratio)** — the ratio between the highest and lowest per-site
  geometric-mean performance for an ISP. Strong intuitive meaning: 1.0 is ideal,
  1.15 = 15% different. Under some conditions it can badly underestimate the
  difference between two sites.
- **KSdistance × 10** — the maximum Kolmogorov–Smirnov distance between the
  per-site CDFs for an ISP ([Wikipedia](https://en.wikipedia.org/wiki/Kolmogorov%E2%80%93Smirnov_test)).
  More robust than the ratio but with poor intuitive meaning; scaled ×10 so 0 is
  best and 10 is worst (fully non-overlapping distributions).

Low scores on both are good.

## Controls

| Control | Effect |
|---|---|
| **Client ISPs** | number of top-ranked ISPs (by test volume) to include. Too many pulls in tiny ISPs with poor connectivity and noisier results — balance is key. |
| **Verbose** | includes single-server metros, which have no comparison and show a spread of 0. |

The date range is not an active selector — it shows the date range of the cached
pre-computed data.

## Navigation

Click a bar for a metro and a link appears below the chart; click that link to
open the Regional Details dashboard for that metro, with the anchor pre-set.
