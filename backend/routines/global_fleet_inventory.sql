CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.global_fleet_inventory(startDate DATE, endDate DATE)
AS
WITH
# 20250905GFI Create fcn global_fleet_inventory

# Tester
# SELECT * FROM `mm_preproduction.global_fleet_inventory`('2026-01-10', '2026-01-16')

ndt7_DS16 AS (
  SELECT
    FORMAT('%t-%t', server.site, server.machine ) AS server,
    server.site,
    REGEXP_EXTRACT(server.site, r'^[a-z]{3}') AS metro,
    date,
    (   IFNULL((SELECT MAX(TCPinfo.BytesSent) FROM UNNEST(raw.Download.ServerMeasurements)), 0.0) +
        IFNULL((SELECT MAX(TCPinfo.BytesSent) FROM UNNEST(raw.Upload.ServerMeasurements)), 0.0)
    ) / 1000.0 AS sentkB, # Includes both download payload and upload ACKs
    (raw.download IS NOT NULL) AS isDownload,
  FROM (
    SELECT date, server, raw FROM `mm_preproduction.ndt7_DS16` UNION ALL
    (SELECT date, server, raw FROM `mm_preproduction.autoload_DS16`)
  )
  WHERE date BETWEEN startDate and endDate
  # WHERE date BETWEEN DATE_SUB("$endDate", INTERVAL ${duration:raw}-1 DAY) and DATE("$endDate")
  AND server.site IS NOT NULL # XXX Why is this missing somplace
),

serverMetadata AS (
  SELECT *,
    0.10 AS estPrice
  FROM (
    SELECT
    Server, site,
    latitude, longitude,
    FORMAT('%t %t',City, CountryCode) AS Loc,
    CASE
      WHEN org = 'googleoim' THEN 'OIM'
      WHEN zone IS NOT NULL AND loadBalanced = 'false' THEN 'GCE' 
      WHEN zone IS NOT NULL AND loadBalanced = 'true' THEN 'GCE-LB' 
      WHEN managed LIKE '%machine%' AND manual = 'donatedTransit' THEN 'Legacy-Other'
      WHEN managed LIKE '%machine%' THEN 'Legacy-Google'
      WHEN deployment LIKE 'byos' AND type LIKE 'physical' THEN 'byos-physical'
      WHEN deployment LIKE 'byos' AND type LIKE 'virtual' THEN 'byos-virtual'
      ELSE 'UNKNOWN'
    END as MLabSKU,
    REGEXP_EXTRACT(zone, '[a-z]*') AS extSKU,  -- External providers SKU (or zone, etc)
    tag,
    managed, deployment, org, type, machineType, zone, loadBalanced,
    FROM `mlab-collaboration.mm_preproduction.cached_metadata`
  )
),

-- Everything we know about each server
serverData AS ( -- Trafic is normailized to per day
  SELECT *,
    SUM(tests) OVER (PARTITION BY metro) AS metroTests,
    SUM(egressMBpD) OVER (PARTITION BY metro) AS metroEgressMBpD,
  FROM (
    SELECT
      metro, server,
      COUNTIF(isDownload) * 1.0 / COUNT (DISTINCT date) *16 AS tests,  -- DS normalized downloads per day
      SUM(sentkB) /1000.0 / COUNT (DISTINCT date) *16 AS egressMBpD,  -- DS normalized Bytes per day (includes upload ACKs)
    FROM ndt7_DS16
    GROUP BY metro, server
  ) LEFT OUTER JOIN serverMetadata USING (server)
),

######################################################
serverStats AS (
  SELECT
    metro, Loc, latitude, longitude, tag, server, site, MLabSKU,
    REGEXP_extract(tag,'[A-Z]*') AS cont,
    ROUND(tests) AS dailyTests,
    ROUND(egressMBpD) AS egressMBpD,
    ROUND(100.0*tests / metroTests ) AS pctTests,
    ROUND(100.0*egressMBpD / metroEgressMBpD ) AS pctEgress,
    ROUND( egressMBpD * 8 / (24*60*60)  ) AS egressMBps,
    ROUND( egressMBpD * 30.0 / 1000000.0 ) AS egrMonthlyTiB,
    estPrice,
    ROUND( egressMBpD * estPrice / 1000.0 ) AS estDailyCostUSD,
    # ROUND( egressMBpD * estPrice / 1000000.0 * 30 ) AS estMonthlyCostKUSD,
    ROUND( egressMBpD * estPrice / 1000000.0 * 365 ) AS estAnnualCostKUSD,
  FROM serverData
),

######################################################
siteInventory AS (
  SELECT
    site AS metro,
    'Site' AS level,
    MLabSKU,
    ANY_VALUE(Loc) AS Loc,
    ANY_VALUE(latitude) AS lat, # was latitude,
    ANY_VALUE(longitude) AS long, # was longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    COUNT (DISTINCT metro) AS metros,
    SUM(dailyTests) AS TpD, # wsa dailyTests,
    SUM(pctTests) AS pctT, # was pctTests,
    SUM(pctEgress) AS  pctE, # was pctEgress,
    SUM(egressMbps) AS Mbps, # was egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS TBpM, # was egrMonthlyTiB,
    ANY_VALUE(estPrice) AS Pr, # was estPrice,
    SUM(estDailyCostUSD) AS DpD, # was estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS kDpY, # was estAnnualCostKUSD,
    FORMAT('%t ',tag) AS tag, -- XXX brittle
    FROM serverStats
  GROUP BY site, MLabSKU, tag -- NOOP, because MLabSKU and tag are constant per site
),

metroInventory AS (
  SELECT
    metro,
    'Subtotal by Metro' AS level,
    CAST(NULL AS STRING), -- SP is important
    ANY_VALUE(Loc) AS Loc,
    ANY_VALUE(latitude) AS latitude,
    ANY_VALUE(longitude) AS longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    COUNT (DISTINCT metro) AS metros,
    SUM(dailyTests) AS dailyTests,
    SUM(pctTests) AS  pctTests,
    SUM(pctEgress) AS  pctEgress,
    SUM(egressMbps) AS  egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS egrMonthlyTiB,
    ANY_VALUE(estPrice) AS estPrice,
    SUM(estDailyCostUSD) AS estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS estAnnualCostKUSD,
    FORMAT('%t ',SUBSTR(MIN(tag), 0, 9)) AS tag, -- XXX brittle
    FROM serverStats
  GROUP BY metro
),

metroSkuInventory AS (
  SELECT
    metro,
    'Subtotal by Metro-SKU' AS level,
    MLabSKU,
    ANY_VALUE(Loc) AS Loc,
    ANY_VALUE(latitude) AS latitude,
    ANY_VALUE(longitude) AS longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    1 AS metros,
    SUM(dailyTests) AS dailyTests,
    SUM(pctTests) AS  pctTests,
    SUM(pctEgress) AS  pctEgress,
    SUM(egressMbps) AS  egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS egrMonthlyTiB,
    ANY_VALUE(estPrice) AS estPrice,
    SUM(estDailyCostUSD) AS estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS estAnnualCostKUSD,
    FORMAT('%t ',SUBSTR(MIN(tag), 0, 9)) AS tag, -- XXX brittle
  FROM serverStats
  GROUP BY metro, MLabSKU
),

skuInventory AS (
  SELECT
    CAST(NULL as STRING) AS metro,
    'Subtotal by MLabSKU' AS level,
    MLabSKU,
    CAST(NULL as STRING) AS Loc,
    NULL AS latitude,
    NULL AS longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    NULL AS metros,
    SUM(dailyTests) AS dailyTests,
    NULL AS  pctTests,
    NULL AS  pctEgress,
    SUM(egressMbps) AS  egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS egrMonthlyTiB,
    NULL AS estPrice,
    SUM(estDailyCostUSD) AS estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS estAnnualCostKUSD,
    '!SKU' AS tag,
  FROM serverStats
  GROUP BY MLabSKU
),

contInventory AS (
  SELECT
    CAST(NULL as STRING) AS metro,
    'Subtotal by Continent' AS level,
    CAST( NULL as STRING) AS MLabSKU,
    cont AS Loc,
    NULL AS latitude,
    NULL AS longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    COUNT (DISTINCT metro) AS metros,
    SUM(dailyTests) AS dailyTests,
    NULL AS  pctTests,
    NULL AS  pctEgress,
    SUM(egressMbps) AS  egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS egrMonthlyTiB,
    NULL AS estPrice,
    SUM(estDailyCostUSD) AS estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS estAnnualCostKUSD,
    FORMAT ('!!%t', cont) AS tag
  FROM serverStats
  GROUP BY cont
),
grandInventory AS (
  SELECT
    CAST(NULL as STRING) AS metro,
    'Grand Total' AS level,
    CAST(NULL as STRING) AS MLabSKU,
    cast(null as string) AS Loc,
    NULL AS latitude,
    NULL AS longitude,
    COUNT (DISTINCT server) AS servers,
    COUNT (DISTINCT site) AS sites,
    COUNT (DISTINCT metro) AS metros,
    SUM(dailyTests) AS dailyTests,
    NULL AS  pctTests,
    NULL AS  pctEgress,
    SUM(egressMbps) AS  egressMbps,
    SUM(egressMBpD) / SUM(dailyTests) AS kBpT,
    SUM(egrMonthlyTiB) AS egrMonthlyTiB,
    NULL AS estPrice,
    SUM(estDailyCostUSD) AS estDailyCostUSD,
    SUM(estAnnualCostKUSD) AS estAnnualCostKUSD,
    '!!!' AS tag
  FROM serverStats
),

report AS (
  SELECT
    * FROM SiteInventory
    UNION ALL (SELECT * FROM metroSkuInventory)
    UNION ALL (SELECT * FROM metroInventory)
    UNION ALL (SELECT * FROM skuInventory)
    UNION ALL (SELECT * FROM contInventory)
    UNION ALL (SELECT * FROM grandInventory)
  ORDER BY tag, MLabSKU
)

Select * FROM report;
