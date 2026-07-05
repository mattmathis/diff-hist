CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.cached_metro_report(field STRING, metroRegex STRING, ispCount INT64)
AS
WITH
# 202501GID L41 Create fcn metro_report_from_cached_histograms

# CREATE OR REPLACE TABLE FUNCTION mm_preproduction.cached_metro_report (field STRING, metroRegex STRING, ispCount INT) AS (

# This report computes metrics of the uniforimity of the sites within each metro relative to a set of client ISPs

# Useful quick test
# SELECT * FROM mm_preproduction.cached_metro_report ('MinRTT', 'ams.*', 6)
# Example dashboard query
# SELECT * FROM `mlab-collaboration.mm_preproduction.cached_metro_report` ('MinRTT', "${site:regex}", ${rank})

# CAUTION TESTING IN PROD TESTING IN PROD TESTING IN PROD TESTING IN PROD TESTING IN PROD
# merely compiling this code has the potential break the public grafana dashboard
# devReport AS (  -- Replace this entire query with the dev query (for A/B testing the code here)
#   SELECT *
#   FROM `mlab-collaboration.mm_preproduction.metro_report` ('cached', 'log', 50, field, '2010-01-01', '2010-01-01', metroRegex, ispCount)
# ),

--paste--  EDITED


  src AS ( 
    SELECT
      metro, site, ISPname, ISPrank, timeBin, bin,
      hist,
      metroTests, metroStart, metroEnd, -- per Metro summary statistics, computed upstream
    # FROM `mlab-collaboration.mm_preproduction.access_ndt7_isp_histograms`
    #      (method, xAxis, binSize, field, startDate, endDate, metroRegex, ispCount)
    FROM `mlab-collaboration.mm_preproduction.access_ndt7_cached_histograms`(field, metroRegex, ispCount) -- HARDCODED
  ),

  squashTimeBins AS (  -- Squash out timeBin
    SELECT
      metro, site, ISPname, ISPrank,
      bin,
      metroTests, metroStart, metroEnd,
      SUM(hist) AS hist,
    FROM src
    GROUP BY
      metro, site, ISPname, ISPrank,
      bin,
      metroTests, metroStart, metroEnd
  ),

  site_ISP_cdf AS (  -- Compute pdf, and add summary stats per metro, site, ISPname (msi) group
    -- This is the last processing step that includes hist
    -- Compute traffic percent, cdf and Gmean per MSI
    SELECT metro, site, ISPname, ISPrank, bin,
      SUM(hist) AS tests,
      ROUND(100 * SUM(SUM(hist)) OVER (msi) /
        MIN(MIN(metroTests)) OVER (PARTITION BY metro), 1) AS percent,  
      ROUND(100 * SUM(SUM(hist)) OVER (msi) /
        MAX(MAX(metroTests)) OVER (PARTITION BY metro), 1) AS _percent, 
      SUM(SUM(hist)) OVER (msi ORDER BY bin ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW ) /
        SUM(SUM(hist)) OVER (msi) AS cdf,
      POW(10.0, SUM(SUM(hist*log10(bin))) OVER (msi) /
                SUM(SUM(hist)) OVER (msi) ) AS Gmean,
      MAX(MAX (metroTests)) OVER (PARTITION BY metro) AS metroTests,
      ANY_VALUE(ANY_VALUE(metroStart)) OVER (PARTITION BY metro) AS metroStart,
      ANY_VALUE(ANY_VALUE(metroEnd)) OVER (PARTITION BY metro) AS metroEnd,
    FROM squashTimeBins
    GROUP BY metro, site, ISPname, ISPrank, bin
    WINDOW msi AS ( partition BY metro, site, ISPname, ISPrank) -- All data of one Site and Client ISP
  ),

  metroKS AS ( -- Compute spread, delta for KSdistance
    -- This is the last processing step that includes site and siteName
    -- Compute KSdelta per bin and spread for an ISP within a metro
    SELECT metro, ISPname, ISPrank, bin,
      FORMAT('%t', COUNT(*)) AS Sites, -- sites in the metro as string
      ANY_VALUE(percent) AS percent,
      SUM(tests) AS tests, -- per msi total
      # WAS ANY_VALUE(Gmean) AS Gmean,
      MAX(Gmean) AS Gmean,
      MIN(Gmean) AS _Gmean,
      MAX(cdf) - MIN(cdf) AS delta,  -- Compute delta per bin across msi above
      ANY_VALUE(metroTests) AS metroTests,
      MIN(metroStart) AS metroStart,
      MAX(metroEnd) AS metroEnd,
    FROM site_ISP_cdf
    WHERE (percent = _percent OR ERROR("metro_report:metroKS WHERE _percent"))
    GROUP By metro, ISPname, ISPrank, bin
  ),

  MSI_summary AS (
    SELECT
      'MSI' AS level,
      metro,
      Site,
      field AS data,
      SUM(tests) AS tests,
      # Was ANY_VALUE()
      MAX(percent) AS percent,
      MIN(_percent) AS _percent,
      0.0 AS KSdistance,
      # Was ANY_VALUE(Gmean)
      MAX(Gmean) AS Gmean,
      Min(Gmean) AS _Gmean,
      0.0 AS spread,
      ANY_VALUE(ISPrank) AS ISPrank,
      ISPname,
      MIN(metroStart) AS metroStart,
      MAX(metroEnd) AS metroEnd,
    FROM site_ISP_cdf
    GROUP BY metro, ISPname, Site
    HAVING Gmean = _Gmean OR ERROR('metro_report:MSI_Summary HAVING Gmean')
    AND ( percent = _percent OR ERROR("metro_report:MSI_Summary HAVING _percent"))
  ),
 

  ISP_summary AS (  -- Summarize a ISP Within a metro
    -- This is the last per bin (spread) step
    SELECT
      'ISP Summary' AS level,
      metro,
      MAX(Sites) AS Sites,
      field AS data,
      SUM(tests) AS tests,
      ROUND(100.0*SUM(tests) / ANY_VALUE(metroTests),1) AS percent,
      MAX(delta) as KSdistance,
      0.0 AS Gmean,  -- Compute?
      MAX(Gmean)/MIN(_Gmean) AS Spread,
      # ANY_VALUE(spread) AS spread,
      ISPrank,
      ISPname,
      MIN(metroStart) AS metroStart,
      MAX(metroEnd) AS metroEnd,
    FROM metroKS
    GROUP BY metro, ISPname, ISPrank
  ),

  metro_summary AS (  -- summarize an entire metro
    -- This is the last step that uses ISPname and ISPrank
    SELECT
      'Metro Summary' AS level,
      metro,
      FORMAT('%t', MAX(Sites)) AS Sites,  -- Filler
      field AS data,
      SUM(tests) AS tests,
      ROUND(SUM(percent), 1) as percent,
      MAX(KSdistance) as KSdistance,
      0.0 AS Gmean, -- Compute for entire metro?
      MAX(spread) AS spread,
      0 AS ISPrank, 
      FORMAT('0 %t - %t %t', 
        DATE(TIMESTAMP_ADD(MIN(metroStart), INTERVAL 20 SECOND)),   -- Tests seconds before midnight count as tomorrow
        DATE(MAX(metroEnd)),
        #  method -- Selected
        "Cached"  -- HARDCODED
      ) AS SummaryName,
      MIN(metroStart) AS metroStart,
      MAX(metroEnd) AS metroEnd,
    FROM ISP_summary
    GROUP BY metro
  ),

  report AS (  -- Union three differnet summaries
    SELECT *
    FROM (
      SELECT * FROM ISP_summary   UNION ALL
      SELECT * FROM metro_summary UNION ALL
      SELECT * EXCEPT (_Gmean, _percent) FROM MSI_summary
    )
    ORDER BY metro, ISPrank 
  )

  SELECT * FROM report ORDER BY metro, ISPrank, Sites, data;
