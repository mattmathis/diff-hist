<!-- REVIEW: harvested from the hand-written index accordion (no Drive doc). This
     is an internal/operational dashboard that nonetheless appears on the public
     index. Marker tiers below. -->

# Fleet and Egress Load

Egress volume and cost across the M-Lab server fleet — an internal dashboard for
fleet lifecycle management and capacity planning.
<!-- snip:index -->

A world map plus a filterable inventory table let you see where traffic and
serving value are concentrated across the fleet.
<!-- snip:intro -->

## What it shows

**World map** — one marker per metro, coloured by log₁₀(tests/day). Hover for
metro detail: servers, tests/day, TB/month, Mbps, and estimated cost. Select
`metros` and `sites` in Display to include per-site detail in the hover.

**Table** — a filterable inventory at three levels:

| Level | Rows |
|---|---|
| Summaries | global total, per-continent and per-server-type subtotals |
| Metros | one row per metro with aggregate traffic and value |
| Sites | one row per M-Lab site |

Columns include server count, tests/day, average Mbps, kB/test, TB/month,
deployment SKU, and estimated daily/annual value.

## Controls

| Control | Effect |
|---|---|
| **End date** | last day of the sample window; defaults to two days ago (UTC) |
| **Duration** | sample window: 1, 7, or 30 days |
| **Display** | which row levels to show in the table and map hover |

## Value model

Serving value is estimated at a flat $0.10/GB egress — this generally
overestimates value in North America and Europe and underestimates it in the
Pacific rim and most of the Southern Hemisphere. Egress is measured from TCPinfo
`BytesSent` for both download payload and upload ACKs.
