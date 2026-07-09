<!-- REVIEW: converted from the Drive doc "Documentation for Competition Reports"
     (marked DRAFT, revised 2025-10-19). Covers BOTH the minRTT and throughput
     competition report notebooks. Revision: noted the breadcrumb column is a
     clickable Details link. Content is largely a column reference and is mostly
     UI-agnostic, so little else was changed. -->

# Competition Reports

Two dashboards — **Competition Reports for minRTT** and **for Throughput** —
identify other nearby M-Lab servers that outperform your servers to some client
networks. This can be useful when planning peering upgrades.
<!-- snip:index -->

Each row reflects one triplet ⟨your_site, site2, clientISP⟩ where the path from
M-Lab site2 to the client ISP outperforms the path from your M-Lab site (the
target site) to the same client ISP.

- For **minRTT**, the comparison is the difference between the arithmetic means
  of the minRTTs.
- For **throughput**, the comparison is the ratio of the geometric means of the
  throughputs.
<!-- snip:intro -->

## Columns

- **targetSite** — one of your M-Lab servers.
- **targetName** — the name associated with your server's ASN.
- **Amean** — arithmetic mean of minRTT (via NDT) over the path from your server
  to users in the client ISP.
- **Gmean** — geometric mean of NDT throughput over that same path.
- **site2** — some other M-Lab server.
- **Site2name** — the name associated with site2's AS number. This may not match
  site2's hosting organization, e.g. if it uses its upstream provider's origin
  ASN.
- **Amean2** — arithmetic mean of minRTT over the path from site2 to users in
  the client ISP.
- **Gmean2** — geometric mean of NDT throughput over that path.
- **clientISP** — the origin ASN and name of the ISP providing connectivity to
  the users running NDT.
- **KSdistance** — a robust indication of the difference in the shape of the
  distributions ([Wikipedia](https://en.wikipedia.org/wiki/Kolmogorov%E2%80%93Smirnov_test)).
  More robust than the ratio but with poor intuitive meaning; if the ratio is
  small and the KS distance is large, inspect the detailed plots.
- **deltaAmean** — the difference Amean − Amean2, in milliseconds.
- **pctExcess** — deltaAmean as a percentage: 100 × deltaAmean / Amean2. Often
  irrelevant on short paths, where small absolute differences are large relative
  to the actual path length.
- **ratio** — the relative throughputs of the paths, Gmean2 / Gmean. Reads
  directly as a percentage: 1.21 means 21% faster.
- **tests** — the number of NDT tests to your target site from this client ISP.
- **pctLoad** — percentage load on the target site from this client ISP.
  Note: relative to the sum of the *selected* client ISPs only, not the total
  load on the target site.
- **distance_kM** — great-circle distance between the target site and site2
  registered locations (themselves rounded to IATA locations).
- **breadcrumb** — a shorthand summary of how to find the data, shown as a
  clickable **Details** link to the Regional Details dashboard.

Outbound tests from cloud providers are likely from VPN exit nodes. Those
measurements probably reflect the entire end-to-end path from the client through
the tunnel to the M-Lab server, not the path from the cloud provider to the
M-Lab server. There may be tests originating within the cloud provider itself,
but they are not generally identifiable in the data.
