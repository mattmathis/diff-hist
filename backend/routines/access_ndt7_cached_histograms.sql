CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.access_ndt7_cached_histograms(field STRING, metroRegex STRING, ispCount INT64)
AS
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

  SELECT * FROM normalized ORDER BY site, ISPrank, binIX;
