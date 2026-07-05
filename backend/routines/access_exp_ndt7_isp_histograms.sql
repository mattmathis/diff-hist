CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.access_exp_ndt7_isp_histograms(method STRING, xAxis STRING, binSize INT64, field STRING, startDate DATE, endDate DATE, siteRegex STRING, ispCount INT64)
AS
WITH
# 202501GID L30 Create fcn access_exp_ndt7_isp_histograms

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

# Don't do live here, do it at the bottom of the stack

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
),  -- Close choose AS ...

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
    -- WHERE ERROR('Function access_exp_ndt7_isp_histograms() is disabled.  s/_exp_/_/')
    GROUP BY site, ISPname, timeBin, binIX, bin, linBin
    # ORDER BY metro, site,  ISPrank, ISPname, binIX -- DEBUGGING
  )


SELECT * FROM normalized;
