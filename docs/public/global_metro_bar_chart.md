<!-- TODO:  -->

# Global Metro Bar Chart

Interconnect-quality **("pain")** scores for every metro with two or more M-Lab
servers — how differently the top client ISPs are served across the M-Lab servers in the metro. Lower is better; high values suggest some users may struggle to reach
certain local content.

For a new user, this is the best place to start.
<!-- snip:index -->
High scores suggest [Anomalous Topology, Routing Policies, or Congested Interconnections](https://arxiv.org/abs/2603.25875) in the mid-path between the ISPs in the region.

For the top N client (user-access) ISPs in each metro, it finds the worst-case
performance difference between pairs of M-Lab sites serving that ISP. A healthy
interconnection ecosystem produces small differences. MinRTT and throughput are
scored separately: differences in **minRTT** indicate paths longer than
necessary (routing or peering anomalies); differences in **throughput** suggest congestion or
capacity problems between ISPs.
<!-- snip:intro -->

## The two measures

- **Spread (ratio)** — the ratio between the highest and lowest per-site
  geometric-mean performance for an ISP. This has a strong intuitive meaning: 1.0 is ideal,
  1.15 means the two differ by 15%. Under certain conditions spread can badly underestimate the
  difference between two sites.
- **KSdistance × 10** — the maximum Kolmogorov–Smirnov distance between the
  per-site CDFs for an ISP ([Wikipedia](https://en.wikipedia.org/wiki/Kolmogorov%E2%80%93Smirnov_test)).
  More robust than the ratio but it has a poor intuitive meaning; scaled ×10 so 0 is
  best and 10 is worst (e.g. fully non-overlapping distributions).

Low scores on both are good.

## Controls

| Control | Effect |
|---|---|
| **Client ISPs** | number of top-ranked ISPs (by test volume) to include. Too many pulls in tiny ISPs with poor connectivity and noisier results — balance is key. |
| **Verbose** | includes all single-server metros, which have no comparison and show a spread of 0. |

The date range is not an active selector — it shows the date range of the 
pre-computed cached data.

## Navigation

Click a bar for a metro and a link appears below the chart; click that link to
open the Regional Details dashboard with that metro preselected.
