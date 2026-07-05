CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.calibration_report(method STRING, xAxis STRING, binSize INT64, field STRING, startDate DATE, endDate DATE, regionRegex STRING, radius INT64, ispCount INT64)
AS
WITH
# 202501GID L43 Create fcn calibration_report

# This report computes metrics of the uniformity of the sites within each region relative to a set of client ISPs
# It is fully parameterized, and supports experimental features eleswhere in the processing stack
# It calibrates Servers by identifying pairs of servers with minimal differences

# TODO
# Recover and debug calSpread algorithm
# Autoselection modes

# Useful quick test (highlight and execute directly)
# FROM mm_preproduction.calibration_report ('cached', 'none', 50, 'MeanThroughputMbps', '2025-01-28','2025-01-28', 'bom', 100, 5)

# Example grafana dashboard query
# 

  src AS ( 
    SELECT metro, site, siteName, ISPname, timeBin, binIX,
      hist,
      # metroTests, -- XXX We really want siteTests
      # metroStart AS serverStart,  -- Interim patch
      # metroEnd AS serverEnd, -- Interim patch
    FROM `mlab-collaboration.mm_preproduction.access_ndt7_isp_histograms`
          (method, xAxis, binSize, field, startDate, endDate, regionRegex, ispCount)
  ),
  
  site_ISP_cdf AS (  -- Compute pdf, and summary stats per <metric, site, ISPname> (msi) group
    -- Summary stats include traffic volumes and percent
    -- This is the last processing step that includes hist
    SELECT site, siteName, ISPname, binIX,
      ANY_VALUE(geoPoint) AS geoPoint,
      SUM(hist) AS tests,
      SUM(SUM(hist)) OVER () AS totalTests,
      ROUND(100 * SUM(SUM(hist)) OVER (msi) /
        (SUM(SUM(hist)) OVER ())) AS percent,
      SUM(SUM(hist)) OVER (msi ORDER BY binIX ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW ) /
        SUM(SUM(hist)) OVER (msi) AS cdf,
      POW(10.0, SUM(SUM(hist*((( binIX/50.0 )))) ) OVER (msi) /   -- XXX only correct for log scaled metrics
                SUM(SUM(hist)) OVER (msi) ) AS Gmean,
      # MAX(MAX (serverTests)) OVER (PARTITION BY server) AS serverTests,
      # ANY_VALUE(ANY_VALUE(serverStart)) OVER (PARTITION BY site) AS serverStart,
      # ANY_VALUE(ANY_VALUE(serverEnd)) OVER (PARTITION BY site) AS serverEnd,
    FROM src 
      LEFT JOIN ( -- Add site metadata: geo coordinates
        SELECT Site, ST_GEOGPOINT(Longitude, Latitude) geoPoint
        FROM `mlab-collaboration.mm_preproduction.cached_metadata`)
      USING (Site)
    GROUP BY site, siteName, ISPname, binIX
    HAVING MAX(timeBin) = MIN(timeBin) OR ERROR('calibration_report: site_ISP_cdg: non constant timeBin')
    WINDOW msi AS ( partition BY site, ISPname) -- All data of one Site and Client ISP
  ),

triplets AS ( -- 2 servers and a clientISP
  SELECT
    L.site AS targetSite, L.siteName AS targetISPname,
    R.site AS site2, L.ISPname,
    MAX(MAX(ABS(L.cdf - R.cdf)))  OVER (triplet) AS KSdistance,
    MAX(R.Gmean/L.Gmean) AS ratio, -- Inverted relative to other dashboards
    # MAX(L.Gmean/R.Gmean) AS ratio,
    IF(ANY_VALUE(L.Gmean) > ANY_VALUE(R.gmean), 'better', 'worse') AS change,
    GREATEST(MAX(L.Gmean), MAX(R.gmean))/least(MIN(L.Gmean), MIN(R.Gmean)) AS spread,
    ST_DISTANCE(ANY_VALUE(L.geoPoint), ANY_VALUE(R.geoPoint)) /1000.0 AS distance,
    FORMAT('%t %t %t', L.site, R.site, L.ISPname) AS breadcrumb
  FROM site_ISP_cdf AS L JOIN (SELECT site, geoPoint, ISPname, Gmean, binIX, cdf FROM site_ISP_cdf) AS R
    ON (L.site != R.site
      AND L.ISPname = R.ISPname
      AND ST_DISTANCE(L.geoPoint, R.geoPoint) < radius*1000.0)
      AND L.binIX = R.binIX
  GROUP BY targetSite, targetISPname, site2, L.ISPname
  HAVING (MAX(L.gmean) = MIN(L.Gmean)) OR ERROR("calibration_report: L.gmean")
  WINDOW triplet AS (PARTITION BY L.Site, R.site, L.ISPname)
  
),

score AS (
  SELECT targetSite, targetISPname,
    MIN(KSdistance) AS KSdistance,
    # MIN_BY(inverse, KSdistance) AS inverse,
    MIN_BY(ratio, KSdistance) AS ratio,
    MIN_BY(change, KSdistance) AS change,
    # MIN_BY(spread, KSdistance) AS spread,
    MIN_BY(distance, KSdistance) AS distance_km,
    count(*) triplets,
    MIN_BY(breadcrumb, KSdistance) AS breadcrumb,
    FORMAT('var-anchor=%t&var-region=%t&var-region=%t&var-ClientISP=%t',
      REGEXP_EXTRACT(MIN_BY(breadcrumb, KSdistance), "^([a-z]{3})"),  -- Anchor metro
      REGEXP_EXTRACT(MIN_BY(breadcrumb, KSdistance), "^([a-z0-9]+) "),  -- targetSite
      REGEXP_EXTRACT(MIN_BY(breadcrumb, KSdistance), " ([a-z0-9]+) "),  -- otherSite
      REGEXP_EXTRACT(MIN_BY(breadcrumb, KSdistance), "[a-z0-9]+ [a-z0-9]+ (.*)")  ) AS BCargs,
  FROM triplets
  GROUP BY targetSite, targetISPname
)

SELECT * FROM score ORDER BY ratio desc;
