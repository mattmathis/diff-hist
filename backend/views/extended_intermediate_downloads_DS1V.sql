CREATE VIEW `mlab-collaboration.mm_preproduction.extended_intermediate_downloads_DS1V`
AS # View mlab-collaboration.mm_preproduction.extended_intermediate_downloads_DS1V
# edited from: # 20251122PUD create fcn extended_intermediate_downloads_DS16

# This is a "schema compatable" view that mimics the downsampled tables.
# It supports all of the same columns and features, except
# precomputing everything in advance for performance

WITH

src AS (  -- CAUTION these are not production sources
  SELECT
    * EXCEPT ( Parser ), 
    [ parser.ArchiveURL ] AS ArchiveURL,
    'legacy' AS _fleet,
  FROM `measurement-lab.ndt.ndt7_legacy`

UNION ALL
  SELECT
    * EXCEPT ( Archiver ),
    [Archiver.ArchiveURL ] AS ArchiveURL,
    'autojoin' AS _fleet,
  FROM `measurement-lab.ndt.ndt7_dynamic`
),

ndt7downloads AS (
  SELECT *,
    raw.Download.ServerMeasurements[SAFE_ORDINAL(ARRAY_LENGTH(raw.Download.ServerMeasurements))]
        AS FinalSnapshot,
    False AS IsError,   -- TODO ndt-server/issues/317
    TIMESTAMP_DIFF(raw.Download.EndTime, raw.Download.StartTime, MILLISECOND)*1.0 AS test_duration
  FROM src
  WHERE -- Limit to rows with valid S2C
    raw.Download IS NOT NULL
    AND raw.Download.UUID IS NOT NULL
    AND raw.Download.UUID NOT IN ( '', 'ERROR_DISCOVERING_UUID' )
),

PreComputeNDT7 AS (
  SELECT
    -- All std columns top levels
    id, date, ArchiveURL, server, client, a, raw,  -- parser removed

    -- Computed above, due to sequential dependencies
    IsError, FinalSnapshot, test_duration,

    FinalSnapshot IS NOT NULL AS IsComplete, -- Not Missing any key fields

    IF ("early_exit" IN (SELECT metadata.Name FROM UNNEST(raw.Download.ClientMetadata) AS metadata), True, False) AS IsEarlyExit,

    -- Protocol
    CONCAT("ndt7",
      IF(raw.ClientIP LIKE "%:%", "-IPv6", "-IPv4"),
      CASE raw.ServerPort
        WHEN 443 THEN "-WSS"
        WHEN 80 THEN "-WS"
        ELSE "-unknown" END ) AS Protocol,

    -- TODO(https://github.com/m-lab/etl/issues/893) generalize IsOAM
    ( raw.ClientIP IN
        ( "35.193.254.117", -- script-exporter VMs in GCE, sandbox.
          "35.225.75.192", -- script-exporter VM in GCE, staging.
          "35.192.37.249", -- script-exporter VM in GCE, oti.
          "23.228.128.99", "2605:a601:f1ff:fffe::99", -- ks addresses.
          "45.56.98.222", "2600:3c03::f03c:91ff:fe33:819", -- eb addresses.
          "35.202.153.90", "35.188.150.110" -- Static IPs from GKE VMs for e2e tests.
        ) ) AS IsOAM,

    -- TODO(https://github.com/m-lab/k8s-support/issues/668) deprecate? _IsRFC1918
    ( (NET.IP_TRUNC(NET.SAFE_IP_FROM_STRING(raw.ClientIP),
                8) = NET.IP_FROM_STRING("10.0.0.0"))
      OR (NET.IP_TRUNC(NET.SAFE_IP_FROM_STRING(raw.ClientIP),
                12) = NET.IP_FROM_STRING("172.16.0.0"))
      OR (NET.IP_TRUNC(NET.SAFE_IP_FROM_STRING(raw.ClientIP),
                16) = NET.IP_FROM_STRING("192.168.0.0"))
    ) AS _IsRFC1918,

/*
    REGEXP_CONTAINS(parser.ArchiveURL,
      'mlab[1-3]-[a-z][a-z][a-z][0-9][0-9]') AS IsProduction,
*/  True AS IsProduction,   --- XXX need a better test, or is this redundant?
/*
    -- Obsolete _IsCongested and _IsBloated, used by IsValid2021
    (FinalSnapshot.TCPInfo.TotalRetrans > 0) AS _IsCongested,
    ((FinalSnapshot.TCPInfo.RTT > 2*FinalSnapshot.TCPInfo.MinRTT) AND
      (FinalSnapshot.TCPInfo.RTT > 1000)) AS _IsBloated,
      */
  _fleet -- XXX testcode
  FROM
    ndt7downloads
),

UnifiedDownloadSchema AS (
  SELECT
    id,   -- col 1
    date,   -- col 2
    STRUCT (
      a.UUID,
      a.TestTime,
      'Download' AS Direction,
      a.CongestionControl,
      a.MeanThroughputMbps,
      a.MinRTT,  -- mS
      a.LossRate
    ) AS a,  -- col 3

    STRUCT (
      'extended_ndt7_downloads' AS View,
      Protocol,
      raw.Download.ClientMetadata AS ClientMetadata,
      raw.Download.ServerMetadata AS ServerMetadata,
      ArchiveURL
    ) AS metadata, -- col 4

    -- Struct filter has predicates for various cleaning assumptions
    STRUCT (
      IsComplete, -- Not Missing any key fields
      IsProduction,     -- Not mlab4, abc0t, or other pre production servers

      IsError,                  -- Server reported a problem
      IsOAM,               -- internal testing and monitoring
      -- _IsRFC1918,            -- Not a real client (deprecate?)
      False AS IsPlatformAnomaly, -- FUTURE, No switch discards, etc
      (FinalSnapshot.TCPInfo.BytesAcked < 8192) AS IsSmall, -- not enough data
      (test_duration < 9000.0) AS IsShort,   -- Did not run for enough time
      (test_duration > 60000.0) AS IsLong,    -- Ran for too long
      IsEarlyExit   -- Short tests may be allowed when early-exit is true
      -- _IsCongested,  -- XXX
      -- _IsBloated -- XXX
    ) AS filter, -- col 5

    STRUCT (
      -- TODO(https://github.com/m-lab/etl-schema/issues/141) Relocate IP and port
      raw.ClientIP AS IP,
      raw.ClientPort AS Port,
      client.Geo,
      client.Network
    ) AS client, -- col 6

    STRUCT (
      -- TODO(https://github.com/m-lab/etl-schema/issues/141) Relocate IP and port
      raw.ServerIP AS IP,
      raw.ServerPort AS Port,
      server.Site, -- e.g. lga02
      server.Machine, -- e.g. mlab1
      server.Geo,
      server.Network
    ) AS server, -- col 7
  
    _fleet, -- Test code XXX where does this belong?

    PreComputeNDT7 AS _internal202511 -- col 8 Not stable and subject to breaking changes

  FROM PreComputeNDT7
),

-- We compute this here (rather than up a level so it can be materialized)
UnifiedIsValid AS (
  SELECT *,
    ( filter.IsComplete -- Not missing any important fields
    AND filter.IsProduction -- not a test server
    AND NOT filter.IsError -- Server reported an error
    AND NOT filter.IsOAM -- operations and management traffic
    AND NOT filter.IsPlatformAnomaly -- overload, bad version, etc
    AND NOT filter.IsSmall -- less than 8kB data
    AND (NOT filter.IsShort OR filter.IsEarlyExit) -- insufficient duration or early exit.
    AND NOT filter.IsLong -- excessive duraton
    -- TODO(https://github.com/m-lab/k8s-support/issues/668) deprecate? _IsRFC1918
    -- AND NOT filter._IsRFC1918
  ) AS IsValidBest
FROM UnifiedDownloadSchema
)

-- Add the clustering columns used tin the tables, although they are noops here
SELECT *, 
  server.Geo.ContinentCode AS ServerContinent,
  server.Site AS ServerSite,
FROM UnifiedIsValid

;
