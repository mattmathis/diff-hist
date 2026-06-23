# `access_ndt7_isp_histograms` (table function)

**BigQuery resource:** `mlab-collaboration:mm_preproduction.access_ndt7_isp_histograms`  
**Type:** TABLE_VALUED_FUNCTION

> Auto-generated from the live BigQuery definition by `tools/gen_docs.py`. Review and edit the prose sections; the signature, schema, and definition are authoritative.

## Description

> _Derived from the definition below — please review for accuracy._

The core **per-ISP histogram source** for the dashboards. Returns NDT7
measurement histograms for one metric (`field`), broken down by
`(metro, site, client ISP, time bin, value bin)`.

* **Method dispatch.** `method` selects the processing stack. The `cached`
  branch reads [`access_ndt7_cached_histograms`](access_ndt7_cached_histograms.md);
  the `experimental` branch (reading `experimental_ndt7_isp_histograms`) is
  **currently commented out** in the definition, so today only `cached` returns
  rows.
* **Bin densification.** It generates zero-count rows for every bin index in
  each metro's `[minBinIX, maxBinIX]` range (capped at 1000 bins), so every
  site/ISP shares the same bin axis — important for clean PDF/CDF lines.
* **Bin value.** `bin` is derived from `binIX`: `POW(10, binIX/50)` for normal
  metrics, `1.0*binIX` for `linear*` metrics, `POW(10, binIX/200)` for `fine*`.

Arguments: `siteRegex` selects sites; `ispCount` caps the top-ranked ISPs.
`xAxis`, `binSize`, `startDate`, `endDate` are passed through but **ignored by
the cached branch**. Callers typically filter `ISPname` by a client-ISP regex
and re-thin the bins by `binSize` downstream.

`linBin` is marked DEPRECATED in the source but is still produced.

## Arguments

| # | Name | Type |
|---|------|------|
| 1 | `method` | STRING |
| 2 | `xAxis` | STRING |
| 3 | `binSize` | INT64 |
| 4 | `field` | STRING |
| 5 | `startDate` | DATE |
| 6 | `endDate` | DATE |
| 7 | `siteRegex` | STRING |
| 8 | `ispCount` | INT64 |

## Output columns

| Column | Type |
|--------|------|
| `metro` | STRING |
| `site` | STRING |
| `siteName` | STRING |
| `ASnumber` | INTEGER |
| `ISPname` | STRING |
| `ISPrank` | INTEGER |
| `timeBin` | TIMESTAMP |
| `binIX` | INTEGER |
| `bin` | FLOAT |
| `linBin` | FLOAT |
| `hist` | INTEGER |
| `metroRawTests` | INTEGER |
| `metroTests` | INTEGER |
| `metroStart` | TIMESTAMP |
| `metroEnd` | TIMESTAMP |

## Call sites in this dashboard

- **panel #81 'Download MinRTT (linear mS) per MLab Site for ${ClientISP}'**
    - `method` = `"${method}"`
    - `xAxis` = `"${xAxis}"`
    - `binSize` = `${binSize}`
    - `field` = `"MinRTT"`
    - `startDate` = `DATE(REGEXP_EXTRACT("${__from:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `endDate` = `DATE(REGEXP_EXTRACT("${__to:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `siteRegex` = `"${region:regex}"`
    - `ispCount` = `${ISPcount}`
- **panel #48 'Download MinRTT (mS) per MLab Site for $ClientISP'**
    - `method` = `"${method}"`
    - `xAxis` = `"${xAxis}"`
    - `binSize` = `${binSize}`
    - `field` = `"MinRTT"`
    - `startDate` = `DATE(REGEXP_EXTRACT("${__from:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `endDate` = `DATE(REGEXP_EXTRACT("${__to:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `siteRegex` = `"${region:regex}"`
    - `ispCount` = `${ISPcount}`
- **panel #43 'Download  Mbps per MLab Site for $ClientISP'**
    - `method` = `"${method}"`
    - `xAxis` = `"${xAxis}"`
    - `binSize` = `${binSize}`
    - `field` = `"MeanThroughputMbps"`
    - `startDate` = `DATE(REGEXP_EXTRACT("${__from:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `endDate` = `DATE(REGEXP_EXTRACT("${__to:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `siteRegex` = `"${region:regex}"`
    - `ispCount` = `${ISPcount}`
- **panel #133 'Download  $field per MLab Site for $ClientISP'**
    - `method` = `"${method}"`
    - `xAxis` = `"${xAxis}"`
    - `binSize` = `${binSize}`
    - `field` = `"${field}"`
    - `startDate` = `DATE(REGEXP_EXTRACT("${__from:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `endDate` = `DATE(REGEXP_EXTRACT("${__to:date:iso}", '[0-9]{4}-[0-9]{2}-[0-9]{2}'))`
    - `siteRegex` = `"${region:regex}"`
    - `ispCount` = `${ISPcount}`

## Definition (BigQuery DDL)

```sql
WITH
# 202501GID L30 Create fcn access_ndt7_isp_histograms

# TODO test all downstream pannels: DEPRECATE bin and linBin calculated here

choose AS (
  ( SELECT -- Cached
    metro, site, SiteName,
      ASnumber, ISPname, ISPrank,
      timeBin, binIX, hist,
      metroRawTests, metroTests, metroStart, metroEnd,
    FROM `mlab-collaboration.mm_preproduction.access_ndt7_cached_histograms` (field, siteRegex, ispCount)
    WHERE method like '%cached%'
  )

/* -- When in doubt, disable experimental queries
 UNION ALL
 ( SELECT  -- Experimental
     metro, site, SiteName,
     ASnumber, ISPname, ISPrank,
     timeBin, binIX, hist,
     metroRawTests, metroTests,  metroStart, metroEnd,
   FROM `mlab-collaboration.mm_preproduction.experimental_ndt7_isp_histograms` (method, xAxis, binSize, field, startDate, endDate, siteRegex)
   WHERE method like '%exp%'  -- Support multiple experiemnts
     AND ISPrank <= ISPcount
  )
*/

  ),
  
  bracket AS (  -- Replaces audit
    SELECT *,
      MIN(binIX) OVER (PARTITION  BY metro) AS minBinIX,
      MAX(binIX) OVER (PARTITION  BY metro) AS maxBinIX,  
    FROM choose
  ),

  -- Generate zero bins for all valid bins for all sites and ISPs within a metro
  -- Also pass commom metadata via the zero side of the join to populate 0 rows
  zeros AS (
    SELECT
      metro, site, ISPname, timeBin, binIX, 
      CASE 
        WHEN field LIKE '%linear%' THEN 1.0*binIX
        WHEN field LIKE '%fine%' THEN POW(10.0, binIX/200.0)
        ELSE POW(10.0, binIX/50.0)
      END AS bin,
      1.0*binIX AS linBin, -- DEPCATED HARDCODED linear are unit bins
      ANY_VALUE(siteName) AS siteName,
      ANY_VALUE(ASnumber) AS ASnumber,
      ANY_VALUE(ISPrank) AS ISPrank,
      ANY_VALUE(metroRawTests) AS metroRawTests,
      ANY_VALUE(metroTests) as metroTests,
      ANY_VALUE(metroStart) as metroStart,
      ANY_VALUE(metroEnd) as metroEnd,
      ANY_VALUE(minBinIX) AS minBinIX,  -- DEBUGGING
      ANY_VALUE(maxBinIX) AS maxBinIX,  -- DEBUGGING
    FROM bracket, UNNEST (GENERATE_ARRAY(minBinIX, LEAST(maxBinIX, minBinIX+1000), 1)) AS binIX  -- HARDCODE limit to 1000 bins
    GROUP BY metro, site, ISPname, timeBin, binIX
  ),

  normalized AS (  
    SELECT
      ANY_VALUE(zeros.metro) AS metro,
      zeros.site,
      ANY_VALUE(zeros.siteName) AS siteName,
      ANY_VALUE(zeros.ASnumber) AS ASnumber,
      zeros.ISPname,
      ANY_VALUE(zeros.ISPrank) AS ISPrank,
      timeBin,
      zeros.binIX,
      zeros.bin,
      zeros.linBin,   -- deprecate
      IFNULL(SUM(SUM (hist)) OVER (partition BY site, zeros.ISPname, zeros.timeBin, zeros.binIX), 0)  AS hist,
      ANY_VALUE(zeros.metroRawTests) AS metroRawTests,
      ANY_VALUE(zeros.metroTests) AS metroTests,
      ANY_VALUE(zeros.metroStart) as metroStart,
      ANY_VALUE(zeros.metroEnd) as metroEnd,
      -- ANY_VALUE(zeros.minBinIX) AS minBinIX,  -- DEBUGGING
      -- ANY_VALUE(zeros.maxBinIX) AS maxBinIX,  -- DEBUGGING
    FROM choose RIGHT JOIN zeros USING (site, ISPname, timeBin, binIX)
    GROUP BY site, ISPname, timeBin, binIX, bin, linBin
    # ORDER BY metro, site,  ISPrank, ISPname, binIX -- DEBUGGING
  )


SELECT * FROM normalized
```
