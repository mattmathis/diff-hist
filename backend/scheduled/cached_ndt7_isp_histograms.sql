-- Scheduled query: Create cached_ndt7_isp_histograms
-- transferConfig: projects/1031725934094/locations/us/transferConfigs/69f6a19f-0000-2d38-b20a-d4f547f5814c
-- schedule: every sun 23:00

# 202501GID L20 Create cached_ ndt7_isp_histograms

# Schedule weekly on Sunday 23:00 UTC

# Note that schema changes may require explicit drop tables

# Table of server metadata.
# This query has to be first, because the subsequent queries use the results.

# DROP TABLE  mlab-collaboration.mm_preproduction.cached_metadata;
CREATE OR REPLACE TABLE mlab-collaboration.mm_preproduction.cached_metadata (
  tag STRING, Server STRING, site STRING, 
  ContinentCode STRING, CountryCode STRING, City STRING,
  Latitude FLOAT64, Longitude FLOAT64,
  ASNumber INT64, ASName STRING,
  managed STRING, deployment STRING, Org STRING, type STRING,
  machineType STRING, zone STRING, loadBalanced STRING, networkTier STRING,
  externalIP STRING, externalIPv6 STRING,
  # ServerMetadata ARRAY<STRUCT<Name STRING, Value STRING>> 
  manual STRING,
  FirstDate DATE, LastDate DATE, MissingDays INT
  ) AS
SELECT 
  *
FROM `mlab-collaboration.mm_preproduction.server_metadata`() ;

# DROP TABLE  mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_MinRTT;
CREATE OR REPLACE TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_MinRTT (
  part INT64,
  tag STRING,
  metro STRING, site STRING, siteName STRING, -- MLab site info
  ASnumber INT64, ISPname STRING, ISPrank INT64, -- Client info
  timeBin TIMESTAMP,
  binIX INT64, -- histogram X axis
  hist INT64, -- histogram data
  metroRawTests INT64,
  metroTests INT64,
  metroStart TIMESTAMP,
  metroEnd TIMESTAMP,
  ) PARTITION BY
  RANGE_BUCKET(part, GENERATE_ARRAY(0, 400, 1))
  OPTIONS (require_partition_filter = TRUE) AS
SELECT 
  ISPrank*8+0, 'MinRTT', * EXCEPT ( bin )
FROM `mlab-collaboration.mm_preproduction.unified_ndt7_isp_histograms`('DS1V',  'none', 50, 'MinRTT',
  DATE_SUB(CURRENT_DATE(), INTERVAL 9 day), DATE_SUB(CURRENT_DATE(), INTERVAL 3 day), '.*') ;

# DROP TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_MinRTT_linear;
CREATE OR REPLACE TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_MinRTT_linear (
  part INT64,
  tag STRING,
  metro STRING, site STRING, siteName STRING, -- MLab site info
  ASnumber INT64, ISPname STRING, ISPrank INT64, -- Client info
  timeBin TIMESTAMP,
  binIX INT64, -- histogram X axis
  hist INT64, -- histogram data
  metroRawTests INT64,
  metroTests INT64,
  metroStart TIMESTAMP,
  metroEnd TIMESTAMP,
  ) PARTITION BY
  RANGE_BUCKET(part, GENERATE_ARRAY(0, 400, 1))
  OPTIONS (require_partition_filter = TRUE) AS
SELECT
  ISPrank*8+1,'linearMinRTT', * EXCEPT ( bin )
FROM `mlab-collaboration.mm_preproduction.unified_ndt7_isp_histograms`('DS1V',  'none', 1, 'linearMinRTT',
  DATE_SUB(CURRENT_DATE(), INTERVAL 9 day), DATE_SUB(CURRENT_DATE(), INTERVAL 3 day), '.*') ;

# DROP TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_Throughput;
CREATE OR REPLACE TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_Throughput (
  part INT64,
  tag STRING,
  metro STRING, site STRING, siteName STRING, -- MLab site info
  ASnumber INT64, ISPname STRING, ISPrank INT64, -- Client info
  timeBin TIMESTAMP,
  binIX INT64, -- histogram X axis
  hist INT64, -- histogram data
  metroRawTests INT64,
  metroTests INT64,
  metroStart TIMESTAMP,
  metroEnd TIMESTAMP,
  ) PARTITION BY
  RANGE_BUCKET(part, GENERATE_ARRAY(0, 400, 1))
  OPTIONS (require_partition_filter = TRUE) AS
SELECT
  ISPrank*8+2, 'MeanThroughputMbps', * EXCEPT ( bin )
FROM `mlab-collaboration.mm_preproduction.unified_ndt7_isp_histograms`('DS1V',  'none', 50, 'MeanThroughputMbps',
  DATE_SUB(CURRENT_DATE(), INTERVAL 9 day), DATE_SUB(CURRENT_DATE(), INTERVAL 3 day), '.*') ;

# DROP TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_LossRatio;
CREATE OR REPLACE TABLE mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_LossRatio (
  part INT64,
  tag STRING,
  metro STRING, site STRING, siteName STRING, -- MLab site info
  ASnumber INT64, ISPname STRING, ISPrank INT64, -- Client info
  timeBin TIMESTAMP,
  binIX INT64, -- histogram X axis
  hist INT64, -- histogram data
  metroRawTests INT64,
  metroTests INT64,
  metroStart TIMESTAMP,
  metroEnd TIMESTAMP,
  ) PARTITION BY
  RANGE_BUCKET(part, GENERATE_ARRAY(0, 400, 1))
  OPTIONS (require_partition_filter = TRUE) AS
SELECT
  ISPrank*8+3, 'LossRate', * EXCEPT ( bin )
FROM `mlab-collaboration.mm_preproduction.unified_ndt7_isp_histograms`('DS1V',  'none', 50, 'LossRate',
  DATE_SUB(CURRENT_DATE(), INTERVAL 9 day), DATE_SUB(CURRENT_DATE(), INTERVAL 3 day), '.*') ;

# SELECT ERROR("Not Error: DONE")
# ISPrank*8+4 save for upload throughput
# ISPrank*8+5 save for RTTvariability
