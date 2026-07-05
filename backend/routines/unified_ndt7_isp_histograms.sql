CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.unified_ndt7_isp_histograms(method STRING, xAxis STRING, binSize INT64, field STRING, startDate DATE, endDate DATE, siteRegex STRING)
AS
WITH 

# 202501GID L11 Create fcn unified_ndt7_isp_histograms

# Process ndt7 data from all sources matching date range and siteRegex.
# This yields an unconstrained sparse histogram (No explicit max or min, no zero counts)

# Example query
# SELECT * FROM `mlab-collaboration.mm_preproduction.experimental_ndt7_isp_histograms`('DS16', 'none', 50, 'MeanThroughputMbps', '2025-11-09', '2025-11-19', '.*');

  ndt7_all_sources AS (
    # TODO fix for uploads, don't pass unneed columns (test if it matters)
    # test try: SELECT date, id, a, Server, Client, _internal202511.raw.download.clientMetadata,
    SELECT * EXCEPT ( _internal202511, filter ), _internal202511.raw
    FROM `mlab-collaboration.mm_preproduction.extended_intermediate_downloads_DS16`
    WHERE method LIKE '%DS16%' AND field NOT LIKE '%upload%'
      # AND REGEXP_CONTAINS(ServerSite, FORMAT('^(%t)',siteRegex)) -- ADDED clustering column

         UNION ALL

    (SELECT * EXCEPT ( _internal202511, filter ), _internal202511.raw,
    FROM `mlab-collaboration.mm_preproduction.extended_intermediate_uploads_DS16`
    WHERE method LIKE '%DS16%' AND field LIKE '%upload%'
      # AND REGEXP_CONTAINS(ServerSite, FORMAT('^(%t)',siteRegex)) -- ADDED clustering column
    )      
          UNION ALL

    (SELECT * EXCEPT ( _internal202511, filter ), _internal202511.raw, 
    FROM `mlab-collaboration.mm_preproduction.extended_intermediate_downloads_DS1V`
    WHERE method LIKE '%DS1V%' AND field NOT LIKE '%upload%'
      # AND REGEXP_CONTAINS(ServerSite, FORMAT('^(%t)',siteRegex)) -- ADDED clustering column
    )
          UNION ALL

    (SELECT * EXCEPT ( _internal202511, filter ), _internal202511.raw,
    FROM `mlab-collaboration.mm_preproduction.extended_intermediate_uploads_DS1V`
    WHERE method LIKE '%DS1V%' AND field LIKE '%upload%'
      # AND REGEXP_CONTAINS(ServerSite, FORMAT('^(%t)',siteRegex)) -- ADDED clustering column
    )
  ),

  ndt7_combined AS ( -- Was ndt7_managed
    SELECT
      date,
      a.TestTime,
      Server.Site AS site1,
      Server.Network.ASNumber AS serverASN,
      Server.Network.ASName AS serverASname,
      Client.Network.ASnumber,
      Client.Network.ASname,
      IFNULL(CASE -- Pick the metric
        WHEN field LIKE "%MeanThroughputMbps%" THEN a.MeanThroughputMbps
        WHEN field LIKE "%MinRTT%" THEN a.MinRTT
        WHEN field LIKE "%LossRate%" THEN a.LossRate
        -- WHEN field LIKE "%RTO%" THEN ARRAY_REVERSE(raw.Download.ServerMeasurements)[OFFSET(0)].TCPinfo.RTO
        WHEN field LIKE "%RTO%" THEN (SELECT MAX(TCPinfo.RTO) FROM UNNEST(raw.Download.ServerMeasurements))
        WHEN field LIKE '%MSS%' THEN raw.Download.ServerMeasurements[SAFE_OFFSET(0)].TCPinfo.SndMSS
      END, 1.0) AS metric,
      COALESCE ( -- These are locate flags that spoil traffic symmetry, and might DQ the data
          CONCAT('/',(select ANY_VALUE(value) FROM UNNEST ( raw.download.clientMetadata) WHERE name IN ('site', 'org'))),
          CONCAT('/',(select ANY_VALUE(value) FROM UNNEST ( raw.upload.clientMetadata) WHERE name IN ('site', 'org')))
      ) AS locateFlags,
      CASE  -- Client flags that might alter results. These are OK, if they are orthogonal to Locate
        WHEN method LIKE '%showIPv%' THEN IF (raw.ClientIP LIKE '%:%', '/IPv6', '/IPv4')
        WHEN method LIKE '%showNames%' THEN FORMAT ('/%t', client_name) -- All names, NULL is itself
        WHEN method LIKE '%showName=%' THEN IF (REGEXP_CONTAINS(client_name, REGEXP_EXTRACT(method, "Name=([^=]*)")), FORMAT ('/%t', client_name), NULL)
        WHEN method LIKE '%showEarly%' AND early_exit IS NOT NULL THEN FORMAT ('/EE%t', early_exit)
      END AS clientProperties,
      FARM_FINGERPRINT(CONCAT(id, 'seed1')) AS fingerprint,  -- for dedup
      raw.ClientIP AS IP,  -- for dedup
    FROM (SELECT *,
        (select ANY_VALUE(value) FROM UNNEST ( raw.download.clientMetadata) WHERE name = 'client_name') AS client_name,
        (select ANY_VALUE(value) FROM UNNEST ( raw.download.clientMetadata) WHERE name = 'early_exit') AS early_exit,
        FROM ndt7_all_sources
    )
    WHERE date BETWEEN startDate AND endDate
      AND REGEXP_CONTAINS(Server.Site, FORMAT('^(%t)',siteRegex)) -- Needed when we stop clustering on serverSite
      AND IF(field LIKE '%upload%',
        raw.Upload IS NOT NULL,
        raw.Download IS NOT NULL
      )
  ),
  
local_get_ndt7_data AS (  --- Replicates get_ndt7_data in L11
    SELECT
      *,
      REGEXP_EXTRACT(site1, r'^[a-z]{3}') AS metro,
      (COUNT(*) OVER (PARTITION BY REGEXP_EXTRACT(site1, r'^[a-z]{3}'))) AS metroRawTests,
      (MAX(fingerprint) OVER (PARTITION BY IP, date)) AS maxFingerprint,
      # (ARRAY_AGG( fingerprint ) OVER (PARTITION BY IP, date ) ) AS maxFingerprints
      (ARRAY_AGG( fingerprint ) OVER (PARTITION BY IP, date ) ) AS maxFingerprints
    FROM ndt7_combined
  ),


  ndt7_dedup AS ( -- Deduped ndt7 with exactly the right columns
    SELECT *,
    # FROM `mlab-collaboration.mm_preproduction.get_ndt7_data` (field, startDate, endDate, siteRegex)
    FROM local_get_ndt7_data
    WHERE fingerprint = maxFingerprint
    # WHERE fingerprint = maxFingerprints[0]
    # WHERE fingerprint IN UNNEST(maxFingerprints)
  ),

  ndt7_ISPs AS (  -- Client ISP metadata, counts and rank
    SELECT
      metro, ASnumber,
      TRANSLATE(ANY_VALUE(ASname), './\n()','    ') AS ASname,  -- Remove symbols that are not properly escaped
      # ANY_VALUE(tests) AS tests,
      ROW_NUMBER() OVER (PARTITION BY metro ORDER BY ANY_VALUE(tests) DESC) AS ISPrank
    FROM (
      SELECT
        metro, ASnumber,
        ANY_VALUE(ASname) AS ASname,
        COUNT(*) AS tests,
      FROM ndt7_dedup
      GROUP BY metro, ASnumber
      ORDER BY tests DESC
    )
    GROUP BY metro, ASnumber
  ),

  ndt7_metro AS ( -- Aggregate metro statistics, for summary reports
    SELECT
      metro,
      ANY_VALUE(metroRawTests) AS metroRawTests,
      COUNT(*) AS metroTests,
      MIN(testTime) AS metroStart,
      MAX(testTime) AS metroEnd,
      FROM ndt7_dedup
      GROUP BY metro
  ),
# For testing: SELECT * FROM `mlab-collaboration.mm_preproduction.experimental_ndt7_isp_histograms`('exp+show', 'none', 50, 'MeanThroughputMbps', '2025-10-09', '2025-10-09', 'chs')
  ndt7_hist AS (
    SELECT
      -- MLab metro/site axis
      metro,
      CONCAT(site1, IFNULL(locateFlags, ''), IFNULL(ClientProperties, '')) AS site, -- Use locate and client properties as a site suffix
      FORMAT("%t%t%t %t %t", site1,  IFNULL(locateFlags, ''), IFNULL(ClientProperties, ''), ANY_VALUE(serverASN),
                        IFNULL(ANY_VALUE(serverASname), '-missing-' )) AS SiteName, -- Could be unstable

      -- ClienISP axis
      ASnumber,
      FORMAT ("%t %t", ANY_VALUE(ASnumber),
        IFNULL(TRANSLATE(ANY_VALUE(ndt7_ISPs.ASname), './\n()','    '),  -- Remove symbols that are not properly escaped
              '-missing-' )
        # IFNULL(APPROX_TOP_COUNT(CountryCode,2)[offset(0)].value, '?' )  -- suppress instability
        ) AS ISPname, 
      ANY_VALUE(ISPrank) AS ISPrank,

      -- Time Series
      CASE xAxis 
        WHEN 'none' THEN TIMESTAMP(startDate)
        WHEN 'day' THEN TIMESTAMP(date)
        WHEN 'hour' THEN TIMESTAMP_TRUNC(testtime, HOUR)
        ELSE ERROR("experimental_ndt7_isp_histograms: Invalit xAxis")
      END AS timeBin,
  
      -- Histogram, either log or linear
      CASE
        WHEN field LIKE '%linear%' THEN CAST(metric AS INT64)
        WHEN field LIKE '%fine%' THEN CAST(LOG10(metric)*200.0 AS INT64)
        ELSE CAST(LOG10(metric)*50.0 AS INt64)
      END AS binIX,  -- bin index (interger)     
      count(*) AS hist,

    FROM ndt7_dedup JOIN ndt7_ISPs USING ( metro, ASnumber ) 
    WHERE
      (locateFlags IS NULL OR method LIKE '%showLocate%') AND -- DQ rows with Locate flags, unless they are being shown
      metric > 0.0
    GROUP BY metro, site1, locateFlags, ClientProperties, ASnumber, timeBin, binIX
    # ORDER BY metro, Site, siteName, ASnumber, name, bin -- Optomize downstream OVER sorted partitions
  ),

  histogramAudit AS (
    SELECT *
    FROM ndt7_hist JOIN ndt7_metro USING ( metro )
    WHERE  -- enforce invarients
      (binIX IS NOT NULL OR ERROR('unified_ndt7_isp_histograms: NULL binIX'))
  )

  SELECT
    *,
    -- Reconstruct bin for direct access, but DO NOT store it
    CASE 
        WHEN field LIKE '%%linear%%' THEN 1.0*binIX
        WHEN field LIKE '%%fine%%' THEN POW(10.0, binIX/200.0)
        ELSE POW(10.0, binIX/50.0)
      END AS bin,
  FROM histogramAudit;
