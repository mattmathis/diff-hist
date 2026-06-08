# `regional_report` (table function)

**BigQuery resource:** `mlab-collaboration:mm_preproduction.regional_report`  
**Type:** TABLE_VALUED_FUNCTION

> Auto-generated from the live BigQuery definition by `tools/gen_docs.py`. Review and edit the prose sections; the signature, schema, and definition are authoritative.

## Description

> _Derived from the definition below — please review for accuracy._

Computes summary statistics measuring **how uniform the M-Lab sites within a
region are**, relative to a set of client ISPs, for one metric (`field`). It
draws per-(site, ISP) histograms from
[`access_ndt7_isp_histograms`](access_ndt7_isp_histograms.md), builds a CDF per
(site, ISP), and derives two dispersion measures per client ISP:

* **KSdistance** — the maximum spread between site CDFs at any bin (a
  Kolmogorov–Smirnov-style distance); `KSoutlier` names the high/low site pair.
* **Spread** — the ratio of the max to min per-site geometric mean (`Gmean`);
  `SPoutlier` names the driving site pair.

Results are returned as a single table with three nested aggregation levels,
distinguished by the **`level`** column (the dashboard filters on this):

| `level` | Granularity | Shown when |
|---------|-------------|------------|
| `MSI` | one row per metric × site × ISP | verbose only |
| `ISP Summary` | one row per client ISP in the region | always |
| `Regional Summary` | one row aggregating the whole region | always |

Notes for the converter:

* For `method = 'cached'` the `startDate`/`endDate` arguments are effectively
  **ignored** — the cached branch serves the latest precomputed window.
* `regionRegex` selects which sites (servers) participate; `ispRegexp` filters
  client ISPs by name; `ispCount` caps how many top-ranked ISPs are preprocessed.
* `Gmean` and `Spread` share a column in some levels; `Sites` is a string.

## Arguments

| # | Name | Type |
|---|------|------|
| 1 | `method` | STRING |
| 2 | `xAxis` | STRING |
| 3 | `binSize` | INT64 |
| 4 | `field` | STRING |
| 5 | `startDate` | DATE |
| 6 | `endDate` | DATE |
| 7 | `regionRegex` | STRING |
| 8 | `ispRegexp` | STRING |
| 9 | `ispCount` | INT64 |

## Output columns

| Column | Type |
|--------|------|
| `level` | STRING |
| `Sites` | STRING |
| `data` | STRING |
| `tests` | INTEGER |
| `ISPtests` | INTEGER |
| `percent` | FLOAT |
| `KSdistance` | FLOAT |
| `KSoutlier` | STRING |
| `Gmean` | FLOAT |
| `Spread` | FLOAT |
| `SPoutlier` | STRING |
| `ISPname` | STRING |
| `serverStart` | TIMESTAMP |
| `serverEnd` | TIMESTAMP |

## Call sites in this dashboard

- **panel #20 'Summary Statistics for the top ISPs in $anchor'**
    - `method` = `"${method}"`
    - `xAxis` = `"${xAxis}"`
    - `binSize` = `${binSize}`
    - `field` = `"${table_field}"`
    - `startDate` = `DATE(REGEXP_EXTRACT("${__from:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `endDate` = `DATE(REGEXP_EXTRACT("${__to:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `regionRegex` = `"^(${region:regex})"`
    - `ispRegexp` = `"^(${ClientISP:regex})"`
    - `ispCount` = `${ISPcount}`

## Definition (BigQuery DDL)

```sql
WITH
# 202501GID L43 Create fcn regional_report

# This report computes metrics of the uniformity of the sites within each region relative to a set of client ISPs
# It is fully parameterized, and supports experimental features eleswhere in the processing stack

# TODO: use binIX rather than bin, to allow partition by binIX
# Recover and debug calSpread algorithm
# Replace metroStart by serverStart (needs to be fixed upstream)
# Generalize by backporting L42 Create fcn timeseries_report

# Useful quick test (highlight and execute directly)
# SELECT * FROM mm_preproduction.regional_report ('cached', 'none', 50, 'MinRTT', '2025-01-28','2025-01-28', 'lga.*|iad.*', '.*', 5)
# Example grafana dashboard query
# 

  src AS ( 
    SELECT metro, site, ISPname, timeBin, bin,
      hist,
      metroTests,
      metroStart AS serverStart,  -- Interim patch
      metroEnd AS serverEnd,
    FROM `mlab-collaboration.mm_preproduction.access_ndt7_isp_histograms`
          (method, xAxis, binSize, field, startDate, endDate, regionRegex, ispCount)
    WHERE REGEXP_CONTAINS ( ISPname, ispRegexp )
  ),
  
  site_ISP_cdf AS (  -- Compute pdf, and summary stats per site, ISPname (msi) group
    -- Summary stats include traffic volumes and percent
    -- This is the last processing step that includes hist
    SELECT site, ISPname, bin,
      SUM(hist) AS tests,
      SUM(SUM(hist)) OVER () AS totalTests,
      ROUND(100 * SUM(SUM(hist)) OVER (msi) /
        (SUM(SUM(hist)) OVER ())) AS percent,
      SUM(SUM(hist)) OVER (msi ORDER BY bin ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW ) /
        SUM(SUM(hist)) OVER (msi) AS cdf,
      POW(10.0, SUM(SUM(hist*log10(bin))) OVER (msi) /
                SUM(SUM(hist)) OVER (msi) ) AS Gmean,
      # MAX(MAX (serverTests)) OVER (PARTITION BY server) AS serverTests,
      ANY_VALUE(ANY_VALUE(serverStart)) OVER (PARTITION BY site) AS serverStart,
      ANY_VALUE(ANY_VALUE(serverEnd)) OVER (PARTITION BY site) AS serverEnd,
    FROM src
    GROUP BY site, ISPname, bin
    HAVING MAX(timeBin) = MIN(timeBin) OR ERROR('regional_report: site_ISP_cdg: non constant timeBin')
    WINDOW msi AS ( partition BY site, ISPname) -- All data of one Site and Client ISP
  ),

  siteKS AS ( -- Compute delta for KSdistance
    -- This processing step squashes out site
    -- Compute KSdelta per bin and spread for an client ISP within a site
    SELECT ISPname, bin,
      FORMAT('%t', COUNT(*)) AS Sites, -- sites in the site as string
      ANY_VALUE(percent) AS percent,
      SUM(tests) AS tests, -- per msi total
      ANY_VALUE(totalTests) AS totalTests,
      MAX(cdf) - MIN(cdf) AS delta,  -- Compute delta per bin across msi above
      FORMAT('%t %t', MAX_BY(site, cdf), MIN_BY(site, cdf)) AS KSout,
      MIN(serverStart) AS serverStart,
      MAX(serverEnd) AS serverEnd,
    FROM site_ISP_cdf
    GROUP By ISPname, bin
  ),

  MSI_summary AS (  -- Summary for Metric (field), Site, ISP
    SELECT
      'MSI' AS level,
      Site AS Sites,
      field AS data,
      SUM(tests) AS tests,
      SUM(SUM(tests)) OVER (PARTITION BY ISPname) AS ISPtests,
      # Was ANY_VALUE()
      MAX(percent) AS percent,
      0.0 AS KSdistance,
      '' AS KSoutlier,
      MAX(Gmean) AS Gmean,
      0.0 AS Spread,
      '' AS SPoutlier,
      ISPname,
      MIN(serverStart) AS serverStart,
      MAX(serverEnd) AS serverEnd,
    FROM site_ISP_cdf
    GROUP BY ISPname, Site
  ),
 
  ISPstats AS (
    SELECT
      -- Last processing step using site
      MAX(Sites) AS Sites,
      field AS data,
      SUM(tests) AS tests,
      SUM(tests) AS ISPtests,
      ROUND(100.0*SUM(tests) / ANY_VALUE(totalTests),1) AS percent,
      MAX(delta) AS KSdistance,
      MAX_BY(KSout, delta) AS KSoutlier,
      0.0 AS Gmean,  -- We could compute this anyhow, but it would be confusing
      ISPname,
      MIN(serverStart) AS serverStart,
      MAX(serverEnd) AS serverEnd,
    FROM siteKS
    GROUP BY ISPname
  ),

  ISPspread AS (
    -- Last processing step using site
    SELECT ISPname,
      MAX(Gmean)/MIN(Gmean) AS spread,
      FORMAT('%t %t',MIN_BY(site, Gmean), MAX_BY(site, Gmean)) AS SPoutlier,
    FROM site_ISP_cdf
    GROUP BY ISPname
  ),

  ISP_summary AS (  -- Summarize a Client ISP Within the region
    SELECT
      'ISP Summary' AS level, Sites, data,
      tests, ISPtests, percent,
      KSdistance, KSoutlier,
      0.0 AS Gmean, spread, SPoutlier,
      ISPname, serverStart, ServerEnd    
    FROM ISPstats Join ISPspread USING (ISPname)
  ),

  regional_summary AS (  -- summarize all results for the region
    SELECT
      'Regional Summary' AS level,
      FORMAT('%t', MAX(Sites)) AS Sites,  -- Filler
      field AS data,
      SUM(tests) AS tests,
      SUM(tests) AS ISPtests,
      100.0 as percent,
      MAX(KSdistance) as KSdistance,
      MAX_BY(FORMAT('%t %t',KSoutlier, LEFT(ISPname, 10)), KSdistance) AS KSoutlier,
      0.0 AS Gmean, -- We could compute this anyhow
      MAX(spread) AS spread,  -- Gmean and Spread share a column
      MAX_BY(FORMAT('%t %t',SPoutlier, LEFT(ISPname, 10)), spread) AS SPoutlier,
      FORMAT('0 %t - %t %t', 
        DATE(TIMESTAMP_ADD(MIN(serverStart), INTERVAL 20 SECOND)),   -- Tests seconds before midnight count as tomorrow
        DATE(MAX(serverEnd)),
        method -- Selected
      ) AS SummaryName,
      MIN(serverStart) AS serverStart,
      MAX(serverEnd) AS serverEnd,
    FROM ISP_summary
  ),

  report AS (  -- Union three differnet summaries
    SELECT *
    FROM (
      SELECT * FROM MSI_summary UNION ALL
      SELECT * FROM ISP_summary UNION ALL
      SELECT * FROM regional_summary
    )
  )

  SELECT * FROM report ORDER BY ISPtests DESC, tests DESC
```
