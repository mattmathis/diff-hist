CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.timeseries_report(method STRING, xAxis STRING, binSize INT64, field STRING, startDate DATE, endDate DATE, regionRegex STRING, ispRegexp STRING, ispCount INT64)
AS
WITH
# 202501GID L42 Create fcn timeseries_report

# This report computes metrics of the uniformity of the sites within each region relative to a set of client ISPs
# It is fully parameterized, and supports experimental features eleswhere in the processing stack

# This is cloned from 202501GID L43 Create fcn regional_report
#  (by adding add timeBin to all SELECT and GROUP BY clauses)

# Useful quick test
# SELECT * FROM mm_preproduction.timeseries_report ('experimental', 'hour', 50, 'MinRTT', '2025-07-17','2025-07-31', '^(fra).*', '.*', 5)
# Example dashboard
# SELECT * FROM `mlab-collaboration.mm_preproduction.timeseries_report` ('MinRTT', "${site:regex}", '.*', ${rank})

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
    SELECT site, ISPname, timeBin, bin,
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
    GROUP BY site, ISPname, timeBin, bin
    -- HAVING MAX(timeBin) = MIN(timeBin) OR ERROR('regional_report: site_ISP_cdg: non constant timeBin')
    WINDOW msi AS ( partition BY site, ISPname) -- All data of one Site and Client ISP
  ),

  siteKS AS ( -- Compute delta for KSdistance
    -- This processing step squashes out site
    -- Compute KSdelta per bin and spread for an client ISP within a site
    SELECT ISPname, timeBin, bin,
      FORMAT('%t', COUNT(*)) AS Sites, -- sites in the site as string
      ANY_VALUE(percent) AS percent,
      SUM(tests) AS tests, -- per msi total
      ANY_VALUE(totalTests) AS totalTests,
      MAX(cdf) - MIN(cdf) AS delta,  -- Compute delta per bin across msi above
      FORMAT('%t %t', MAX_BY(site, cdf), MIN_BY(site, cdf)) AS KSout,
      MIN(serverStart) AS serverStart,
      MAX(serverEnd) AS serverEnd,
    FROM site_ISP_cdf
    GROUP By ISPname, timeBin, bin
  ),

  MSI_summary AS (  -- Summary for Metric (field), Site, ISP
    SELECT
      'MSI' AS level,
      Site AS Sites,
      field AS data, timeBin,
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
    GROUP BY ISPname, Site, timeBin
  ),
 
  ISPstats AS (
    SELECT
      -- Last processing step using site
      MAX(Sites) AS Sites,
      field AS data, timeBin,
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
    GROUP BY ISPname, timeBin
  ),

  ISPspread AS (
    -- Last processing step using site
    SELECT ISPname, timeBin,
      MAX(Gmean)/MIN(Gmean) AS spread,
      FORMAT('%t %t',MIN_BY(site, Gmean), MAX_BY(site, Gmean)) AS SPoutlier,
    FROM site_ISP_cdf
    GROUP BY ISPname, timeBin
  ),

  ISP_summary AS (  -- Summarize a Client ISP Within the region
    SELECT
      'ISP Summary' AS level, Sites, data, timeBin,
      tests, ISPtests, percent,
      KSdistance, KSoutlier,
      0.0 AS Gmean, spread, SPoutlier,
      ISPname, serverStart, ServerEnd    
    FROM ISPstats Join ISPspread USING (ISPname, timeBin)
  ),

  regional_summary AS (  -- summarize all results for the region
    SELECT
      'Regional Summary' AS level,
      FORMAT('%t', MAX(Sites)) AS Sites,  -- Filler
      field AS data, timeBin,
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
    GROUP BY  timeBin
  ),

  report AS (  -- Union three differnet summaries
    SELECT *
    FROM (
      SELECT * FROM MSI_summary UNION ALL
      SELECT * FROM ISP_summary UNION ALL
      SELECT * FROM regional_summary
    )
  )

  SELECT * FROM report ORDER BY ISPtests DESC, tests DESC;
