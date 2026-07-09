<!-- TODO: Check for truncated client distributions, automate more calibration tests -->

# The M-Lab Calibration Dashboard

Identifies potentially uncalibrated M-Lab servers by comparing measurement
distributions across nearby sites: if a server is well calibrated, some other
server should give the same result for at least one client ISP in the region.
Low scores are best, high scores indicate problems.

This version is not suitable for general use because it is prone to both false positive
and false negative results.
<!-- snip:index -->

The algorithm scans the selected sites for triplets — a target site, a benchmark
site, and a client ISP — where both sites give similar measures of that ISP.
Target sites with high scores (ratio ≫ 1.0 or KS distance ≫ 0.0) have no matching
site and are suspect for calibration problems. Poor results always need to be
checked with other methods (e.g. Regional Details); since we have no ground
truth, the judgement is somewhat subjective.
<!-- snip:intro -->

## What it shows

- **Scatter plot** — KS distance vs ratio for all server pairs with ratio ≥ 1.0.
  Points with ratio > 2 are clamped to the right edge and drawn as triangles.
- **Calibration report table** — the ranked list of pairs; columns below.

It covers the two most important calibration problems for the fleet: servers with
inadequate performance (underpowered CPUs or too much load on a shared server) and site ISPs with
inadequate upstream connectivity. It does not cover clock quanta and jitter,
time-of-day, or geolocation issues unless they affect overall accuracy. The only actions
available today are to lower an organization's serving probability or ask it to
withdraw; in future its data might also be redacted from the public BigQuery datasets.

The defaults (5 client ISPs, sites within the same metro) are sensitive and relatively
unlikely to yield a false pass, but don't work well for metros with fewer than 3
M-Lab sites. Extending the radius and ISP count can force a comparison, but the
results get harder to interpret.

## Selectors

- **Field** — the metric compared. Throughput is most useful for server and
  interconnection health.
- **Region** — normally all; can select individual metros or grouped metros.
- **Radius** — how far to include candidate benchmark sites. Mainly to force a
  comparison for singleton sites (but these are hard to interpret reliably).
- **ISPcount** — number of client ISPs to scan. More increases the chance of a false pass;
  fewer increases the chance of a false fail during a real peering dispute.
- **Method** — select **cached** if present.

## Columns

- **targetSite** — the M-Lab site being evaluated.
- **targetISPname** — name associated with the target site's ASN.
- **KSdistance** — robust indication of the difference in distribution shape
  ([Wikipedia](https://en.wikipedia.org/wiki/Kolmogorov%E2%80%93Smirnov_test)); if
  the ratio is small but KS distance is large, inspect the detailed plots.
- **Ratio** — benchmark site's geometric-mean throughput ÷ the target site's.
- **Change** — whether the target is better or worse than the benchmark.
- **Distance_km** — distance to the candidate benchmark site.
- **Triplets** — number of ⟨targetSite, benchmarkSite, clientISP⟩ triplets scanned.
- **Breadcrumb** — Shows the Target Site, Benchmark Site, and the ISP used to validate similar results.  These are shown as a clickable link to the Regional Details dashboard to see the details.

## Interpreting results

Sites with enough samples (multiple comparisons and enough tests), high KS
distance, and high ratio are the strongest recalibration candidates — they differ
distributionally from neighbors *and* show a directional gap. High KS distance
but ratio near 1 may be a symmetric difference with no clear problem.

## Future calibration checks

- Confirm the high-performance tail isn't truncated by a client ISP with it's own throughput cap.  This might cause a false
  pass, since the client ISP can't validate performance above its own cap.  (This is the source of risk from testing too many client ISPs.)
- Checks for clock quanta/jitter, time-of-day, and geolocation.
- A systematic way to evaluate the trade-offs around the number of client ISPs.
- A connectivity model for regions (e.g. India) where good regional connectivity
  without good upstream connectivity is acceptable — such sites currently look
  poorly calibrated.

See [M-Lab calibration](https://docs.google.com/document/d/15Bf74MhJfJ5yYbmG5uT6egFBW3zGcnL6jW3hztjOYNw/edit?usp=sharing)
for a general discussion of the broad M-Lab calibration problem.
