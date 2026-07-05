CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.get_ndt7_data(field STRING, startDate DATE, endDate DATE, siteRegex STRING)
AS
WITH 
# 202501GID L10 Create fcn get_ndt7_data

# CREATE OR REPLACE TABLE FUNCTION mm_preproduction.get_ndt7_data (field STRING, startDate DATE, endDate DATE, siteRegex STRING )

# TODO: Add upload and additional metrics
# TODO: Use an array of dates rather than a range to support future stability analysis  

 ndt7_managed AS ( -- Production
    SELECT
      date,
      a.uuid,
      a.TestTime,
      Server.Site,
      Server.Network.ASNumber AS serverASN,
      Server.Network.ASName AS serverASname,
      Client.Network.ASnumber,
      Client.Network.ASname,
      Client.Geo.CountryCode,
      Client.Geo.City,
      CASE
        WHEN field LIKE "MeanThroughputMbps" THEN a.MeanThroughputMbps
        WHEN field LIKE "%%MinRTT" THEN a.MinRTT
        WHEN field LIKE "LossRate" THEN a.LossRate
        ELSE ERROR("get_ndt7_data: Invalid field")
      END AS metric,
      FARM_FINGERPRINT(CONCAT(id, 'seed1')) AS fingerprint,  -- for dedup
      client.IP,  -- for dedup
    FROM `measurement-lab.ndt_intermediate.extended_ndt7_downloads`
    WHERE date BETWEEN startDate AND endDate
     AND REGEXP_CONTAINS(server.Site, siteRegex)
     AND (filter.IsComplete AND filter.IsProduction AND NOT filter.IsError AND
          NOT filter.IsOAM AND NOT filter.IsPlatformAnomaly AND NOT filter.IsSmall AND
          (filter.IsEarlyExit OR NOT filter.IsShort) AND NOT filter.IsLong AND NOT filter._IsRFC1918)
  ),
  
  ndt7_autonode AS (  -- autojoin, includes BYOS
    SELECT
      date,
      a.uuid,
      a.TestTime,
      server.Site as site,
      Server.Network.ASNumber AS serverASN,
      Server.Network.ASName AS serverASname,
      Client.Network.ASnumber,
      Client.Network.ASname,
      Client.Geo.CountryCode,
      Client.Geo.City,
      CASE
        WHEN field LIKE "MeanThroughputMbps" THEN a.MeanThroughputMbps
        WHEN field LIKE "%%MinRTT" THEN a.MinRTT
        WHEN field LIKE "LossRate" THEN a.LossRate
        ELSE ERROR("get_ndt7_data: Invalid field")
      END AS metric,
      FARM_FINGERPRINT(CONCAT(a.uuid, 'seed1')) AS fingerprint, -- for dedup
      raw.ClientIP AS IP,  -- for dedup
    FROM `mlab-autojoin.autoload_v2_ndt.ndt7_union`
    WHERE date BETWEEN startDate AND endDate
     AND REGEXP_CONTAINS(server.Site, siteRegex)
     AND raw.Download IS NOT NULL
     AND ARRAY_LENGTH(raw.Download.ServerMeasurements) > 0 -- IsComplete
     AND NOT (raw.Download.ServerMeasurements[SAFE_ORDINAL(ARRAY_LENGTH(raw.Download.ServerMeasurements))].TCPInfo.BytesAcked < 8192) -- IsSmall
     AND (
      IF("early_exit" IN (SELECT metadata.Name FROM UNNEST(raw.Download.ClientMetadata) AS metadata), True, False) OR
      NOT TIMESTAMP_DIFF(raw.Download.EndTime, raw.Download.StartTime, MILLISECOND) < 9000 -- IsShort
     )
     AND NOT TIMESTAMP_DIFF(raw.Download.EndTime, raw.Download.StartTime, MILLISECOND) > 60000 -- IsLong
     AND server.Site IS NOT NULL
  ),

  ndt7_combined AS (
    SELECT *, 
        REGEXP_EXTRACT(site, r'^[a-z]{3}') AS metro,
        (COUNT(*) OVER (PARTITION BY REGEXP_EXTRACT(site, r'^[a-z]{3}'))) AS metroRawTests,
        (MAX(fingerprint) OVER (PARTITION BY IP, date)) AS maxFingerprint
      FROM (
        SELECT * FROM ndt7_managed UNION ALL
        SELECT * FROM ndt7_autonode
      )
  )

  SELECT * FROM ndt7_combined;
