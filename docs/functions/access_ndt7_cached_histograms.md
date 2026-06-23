# `access_ndt7_cached_histograms` (table function)

**BigQuery resource:** `mlab-collaboration:mm_preproduction.access_ndt7_cached_histograms`  
**Type:** TABLE_VALUED_FUNCTION

> Auto-generated from the live BigQuery definition by `tools/gen_docs.py`. Review and edit the prose sections; the signature, schema, and definition are authoritative.

## Description

> _Derived from the definition below — please review for accuracy._

**Fast, partitioned access to precomputed ("cached") NDT7 ISP histograms.**
This is the storage layer underneath
[`access_ndt7_isp_histograms`](access_ndt7_isp_histograms.md) and is also called
directly by the dashboard's **Client ISP** dropdown query.

* **Partition selection.** Reads the wildcard table
  `cached_ndt7_isp_histogram*`, selecting partitions that encode
  `(metric, ISP-rank)`. The partition base is chosen by `field`
  (`MinRTT`=0, `linearMinRTT`=1, `MeanThroughputMbps`=2, `LossRate`=3) and steps
  by 8 up to `ispCount*8 + 7` — so `ispCount` directly controls how many ranked
  ISPs are scanned.
* **Self-audit.** Built-in assertions `ERROR` out if the cache is malformed:
  the partition `tag` must equal `field`, `ISPrank` must fall in
  `1..ispCount`, and `binIX` must be within the metro's bin range.
* **Densification.** Same zero-bin filling and `bin = POW(10, binIX/50)` value
  derivation as the parent function.

Arguments: `field` (metric), `metroRegex` (selects sites — note it matches the
`site` column), `ispCount` (top-N ISPs). Output columns match
`access_ndt7_isp_histograms`.

## Arguments

| # | Name | Type |
|---|------|------|
| 1 | `field` | STRING |
| 2 | `metroRegex` | STRING |
| 3 | `ispCount` | INT64 |

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

- **variable ${ClientISP}**
    - `field` = `"MinRTT"`
    - `metroRegex` = `"${anchor:regex}"`
    - `ispCount` = `${ISPcount}`
- **panel #133 'Download  $field per MLab Site for $ClientISP'**
    - `field` = `"MeanThroughputMbps"`
    - `metroRegex` = `"${region:regex}"`
    - `ispCount` = `${ISPrank}`

## Definition (BigQuery DDL)

```sql
WITH

# CREATE OR REPLACE TABLE FUNCTION mm_preproduction.access_ndt7_cached_histograms(field STRING, metroRegex STRING, ispCount INT )

# Fast (partitioned) access to cached histograms

  src AS (  -- Prefilter data TODO partition and wildcard
    SELECT
      metro, tag, site, siteName, ASnumber, ISPname, ISPrank, timeBin, binIX, hist, metroRawTests, metroTests, metroStart, metroEnd,
      -- Use the same max for all sites and ISPs in the metro
      MAX(binIX) OVER (PARTITION  BY metro) AS maxBinIX,  
      MIN(binIX) OVER (PARTITION  BY metro) AS minBinIX,
    FROM `mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram*`
    WHERE
      part IN UNNEST(GENERATE_ARRAY(  -- This must agree with column part in cached historgrams
                      CASE field
                        WHEN 'MinRTT' THEN 0
                        WHEN 'linearMinRTT' THEN 1
                        WHEN 'MeanThroughputMbps' THEN 2
                        WHEN 'LossRate' THEN 3
                        else 0 -- Default to MinRTT
                      END,
                      ispCount*8 + 7,
                      8))
      AND REGEXP_CONTAINS(site, metroRegex)
  ),

  audit AS ( -- Built in self check for cached histograms
    SELECT *
    FROM src
    WHERE
      (tag = field OR ERROR (FORMAT('access_ndt7_cached_histograms: Incorrect tag %t %t', tag, ISPrank)))
      AND (ISPrank BETWEEN 1 AND ispCount OR ERROR(FORMAT('access_ndt7_cached_histograms: Incorrect ISPrank %t', ISPrank)))
      AND (binIX BETWEEN minBinIX AND maxBinIX OR ERROR(FORMAT('access_ndt7_cached_histograms: Incorrect BinIX %t %t', minBinIX, maxBinIX)))
  ), 
  
  -- Generate zero bins for all valid bins for all sites and ISPs within a metro
  -- Also pass commom metadata via the zero side of the join to populate 0 rows
  zeros AS (
    SELECT
      site, ISPname, timeBin, binIX,
      CASE 
        WHEN field LIKE '%%linear%%' THEN 1.0*binIX
        WHEN field LIKE '%%fine%%' THEN POW(10.0, binIX/1000.0)
        ELSE POW(10.0, binIX/50.0)
      END AS bin,
      1.0*binIX AS linBin, -- HARDCODED linear are unit bins
      ANY_VALUE(metro) AS metro,
      ANY_VALUE(siteName) AS siteName,
      ANY_VALUE(ASnumber) AS ASnumber,
      ANY_VALUE(ISPrank) AS ISPrank,
      ANY_VALUE(metroRawTests) AS metroRawTests,
      ANY_VALUE(metroTests) as metroTests,
      ANY_VALUE(metroStart) as metroStart,
      ANY_VALUE(metroEnd) as metroEnd,
    FROM audit, UNNEST (GENERATE_ARRAY(minBinIX, LEAST(maxBinIX, minBinIX+1000), 1)) AS binIX  -- HARDCODE < 1000 bins (for linear)
    GROUP BY site, ISPname, timeBin, binIX, bin
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
      zeros.linBin,  -- DEPRECATE, but used downstream
      IFNULL(SUM(SUM (hist)) OVER (partition BY site, zeros.ISPname, zeros.binIX), 0)  AS hist,
      ANY_VALUE(zeros.metroRawTests) AS metroRawTests,
      ANY_VALUE(zeros.metroTests) AS metroTests,
      ANY_VALUE(zeros.metroStart) as metroStart,
      ANY_VALUE(zeros.metroEnd) as metroEnd,
    FROM audit RIGHT JOIN zeros USING (site, ISPname, timeBin, binIX)
    GROUP BY site, ISPname, timeBin, binIX, bin, linBin
    # ORDER BY metro, site,  ISPrank, ISPname, binIX -- DEBUGGING
  )

  SELECT * FROM normalized ORDER BY site, ISPrank, binIX
```
