CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.minRTT_competition_report(method STRING, startDate DATE, endDate DATE, targetRegex STRING, radius INT64, ispCount INT64)
AS
WITH
# 202501GID L45 Create fcn minRTT_competition_report

# This report computes metrics of the performace of the targetISP relative to other Sites based to a set of client ISPs
# It is fully parameterized, and supports experimental features eleswhere in the processing stack

# TODO
# Autoselection modes

# Useful quick test (highlight and execute directly)
# SELECT * FROM mm_preproduction.minRTT_competition_report ('cached', '2025-01-28','2025-01-28', 'equinix', 100, 5)


  src AS ( 
    SELECT metro, site, siteName, ISPname, timeBin, binIX,
      hist,
    FROM `mlab-collaboration.mm_preproduction.access_ndt7_isp_histograms`
          (method, 'none', 50, 'linearMinRTT', startDate, endDate, '', ispCount)
  ),
  
  site_ISP_cdf AS (  -- Compute pdf, and summary stats per <metric, site, ISPname> (msi) group
    -- Summary stats include traffic volumes and percent
    -- This is the last processing step that includes hist
    SELECT site, MD.siteName, ISPname, binIX,
      ANY_VALUE(geoPoint) AS geoPoint,
      SUM(SUM(hist)) OVER (msi) AS tests,
      SUM(SUM(hist)) OVER (PARTITION BY ISPname) AS siteTests,
      ROUND(100 * SUM(SUM(hist)) OVER (msi) /
        (SUM(SUM(hist)) OVER ())) AS percent,
      SUM(SUM(hist)) OVER (msi ORDER BY binIX ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW ) /
        SUM(SUM(hist)) OVER (msi) AS cdf,
      # POW(10.0, SUM(SUM(hist*((( binIX/50.0 )))) ) OVER (msi) /   -- XXX only correct for log scale
      #          SUM(SUM(hist)) OVER (msi) ) AS Gmean,
      SUM(SUM(1.0*hist*binIX)) OVER (msi) / SUM(SUM(hist)) OVER (msi) AS Amean, -- Arithmetic mean
      MIN(1.0*binIX) OVER (msi) AS minMinRTT,  -- Not useful, too man spurious 0&1 values
      FORMAT ('%t %t', Sponsor, MD.siteName) AS siteInfo,
    FROM src 
      LEFT JOIN ( -- Add site metadata: geo coordinates, canonicalized siteNames
        SELECT Site,
          ST_GEOGPOINT(Longitude, Latitude) AS geoPoint,
          Org AS Sponsor,
          ASname AS siteName,  -- Canonicalized 
        FROM `mlab-collaboration.mm_preproduction.cached_metadata`)
      AS MD
      USING (Site)
    GROUP BY site, siteName, Sponsor, ISPname, binIX
    HAVING MAX(timeBin) = MIN(timeBin) OR ERROR('calibration_report: site_ISP_cdg: non constant timeBin')
    WINDOW msi AS ( partition BY site, ISPname) -- All data of one Site and Client ISP
  ),

triplets AS ( -- 2 servers and a clientISP
  SELECT
    L.site AS targetSite,
    L.siteName AS targetName,
    MAX(L.Amean) AS Amean,
    # MIN(L.minMinRTT) AS minMinRTT,
    R.site AS site2,
    R.siteName AS site2name,
    MAX(R.Amean) AS Amean2,
    # MIN(R.minMinRTT) AS minMinRTT2,
    L.ISPname AS clientISP,
    
    -- MAX(MAX(ABS(L.cdf - R.cdf)))  OVER (triplet) AS KSdistance,  -- not so meaningful w/o IX bias
    MAX(L.Amean-R.Amean) AS deltaAmean,
    CAST(ANY_VALUE(100.0*SAFE_DIVIDE((L.Amean-R.Amean),R.Amean)) AS INT64) AS pctExcess,
    # MAX(L.minMinRTT-R.minMinRTT) AS deltaMinMinRTT, -- not useful

    ANY_VALUE(L.tests) AS tests,
    CAST(SAFE_DIVIDE(ANY_VALUE(L.tests),ANY_VALUE(L.siteTests)) * 100 AS INT64) AS pctLoad,
    CAST(ST_DISTANCE(ANY_VALUE(L.geoPoint), ANY_VALUE(R.geoPoint)) /1000.0 AS INT) AS distance_kM,
    FORMAT('%t %t %t', L.site, R.site, L.ISPname) AS breadcrumb
  FROM site_ISP_cdf AS L JOIN (SELECT site, siteName, geoPoint, ISPname, Amean, minMinRTT, binIX, cdf, tests, siteTests FROM site_ISP_cdf) AS R
    ON (L.site != R.site
      AND REGEXP_CONTAINS(L.siteInfo, FORMAT('%t', targetRegex)) -- Prototype: FORMAT is noop
      AND L.ISPname = R.ISPname
      AND ST_DISTANCE(L.geoPoint, R.geoPoint) < radius*1000.0)
      AND L.binIX = R.binIX
  GROUP BY targetSite, L.siteName, site2, site2Name, L.ISPname
  HAVING (MAX(L.Amean) = MIN(L.Amean)) OR ERROR("minRTT_competition_report: L.Amean")
  WINDOW triplet AS (PARTITION BY L.Site, R.site, L.ISPname)
)

SELECT *
FROM triplets
WHERE deltaAmean > 0.0
ORDER BY deltaAmean DESC;
