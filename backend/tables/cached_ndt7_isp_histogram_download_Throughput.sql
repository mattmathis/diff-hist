CREATE TABLE `mlab-collaboration.mm_preproduction.cached_ndt7_isp_histogram_download_Throughput`
(
  part INT64,
  tag STRING,
  metro STRING,
  site STRING,
  siteName STRING,
  ASnumber INT64,
  ISPname STRING,
  ISPrank INT64,
  timeBin TIMESTAMP,
  binIX INT64,
  hist INT64,
  metroRawTests INT64,
  metroTests INT64,
  metroStart TIMESTAMP,
  metroEnd TIMESTAMP
)
PARTITION BY RANGE_BUCKET(part, GENERATE_ARRAY(0, 400, 1))
OPTIONS(
  require_partition_filter=true
);
