CREATE TABLE FUNCTION `mlab-collaboration`.mm_preproduction.server_metadata()
AS
WITH

 # 202501GID L00 Create fcn server_metadata
 # NB: dateSamples are not used.  With DS16 this is cheap enough to run from 2025-03-01 to present

 # Aggregate server metedata from NDT7 data in BQ using date samples

 # In principle this should join data from additional sources:

 # Siteinfo e.g. https://github.com/m-lab/siteinfo
 #       OR https://siteinfo.mlab-oti.measurementlab.net/v2/sites/registration.json
 # Locate registrations e.g. https://mlab-sandbox.appspot.com/v2/siteinfo/registrations
 #
 # A test:
 # SELECT * FROM `mm_preproduction.cached_metadata` WHERE ASName LIKE '%.'
 # FUTURE add site calibration and redaction DB
 # A calibration and redaction DB should require date ranges on records

    ndt7_managed AS (
      SELECT
        date,
        CONCAT(server.Site, '-', Server.Machine) AS server,
        server.site,
        server.Geo.ContinentCode,
        server.Geo.CountryCode,
        server.Geo.City,
        server.Geo.Latitude,
        server.Geo.Longitude,
        Server.Network.ASNumber AS ASNumber,
        TRANSLATE(Server.Network.ASName,'./\n()','    ') AS ASName, # Workaround insufficnet canonicalization later
        CASE 
          WHEN server.Site IN ('akl01', 'ath03', 'atl02', 'bcn01', 'beg01', 'cpt01', 'dfw08',
              'dub01', 'fln01', 'fra03', 'hkg03', 'iad02', 'jnb01', 'lax06', 'lga08', 'lhr04',
              'lju01', 'mex01', 'mex04', 'mia02', 'mil05', 'mnl01', 'mnl02', 'mpm02', 'mty01',
              'nbo01', 'nuq02', 'nuq03', 'ord02', 'par05', 'per01', 'sea08', 'svg01', 'syd03',
              'syd05', 'tdg01', 'trn01', 'tpe01', 'trn02', 'tun01', 'wlg02', 'yqm01', 'yul02',
              'yul06', 'yvr01', 'yvr03', 'ywg01', 'yyc02', 'yyz06' ) THEN 'donatedTransit'
          ELSE ''
        END as manual,   -- Manually curated attributes
        raw.download.serverMetadata AS ServerMetadata,
      # FROM `measurement-lab.ndt_intermediate.extended_ndt7_downloads`
      FROM `mlab-collaboration.mm_preproduction.ndt7_DS16`
      # WHERE date = dateSamples -- TODO multiple date samples
      WHERE date >= '2025-03-01'
      # AND REGEXP_CONTAINS(server.Site, siteRegex)
      AND server.Site IS NOT NULL
    ),
    
    ndt7_autonode AS (  -- includes BYOS
      SELECT
        date,
        CONCAT(server.Site, '-', Server.Machine) AS server,
        server.site,
        server.Geo.ContinentCode,
        server.Geo.CountryCode,
        server.Geo.City,
        server.Geo.Latitude,
        server.Geo.Longitude,
        Server.Network.ASNumber AS ASNumber,
        TRANSLATE(Server.Network.ASName,'./\n()','    ') AS ASName, # Workaround insufficnet canonicalization later
        '' AS manual,   -- Manually curated attributes
        raw.Download.ServerMetadata,
      # FROM `mlab-autojoin.autoload_v2_ndt.ndt7_union`
      FROM `mlab-collaboration.mm_preproduction.autoload_DS16`
      # WHERE date = dateSamples -- TODO multiple date samples
      WHERE date >= '2025-03-01'
      # AND REGEXP_CONTAINS(server.Site, siteRegex)
      AND server.Site IS NOT NULL
    ),  

  ndt7_all AS (  -- Decode all metadata as seperate columns
    SELECT 
      *,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'managed') AS managed,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'deployment') AS deployment,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'org') AS org,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'type') AS type,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'machine-type') AS machineType,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'zone') AS zone,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'loadbalanced') AS loadBalanced,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'network-tier') AS networkTier,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'external-ip') AS externalIP,
      (SELECT value FROM UNNEST(ServerMetadata) WHERE name = 'external-ipv6') AS externalIPv6,
      # TODO capture unknown metadata 
    FROM (
      SELECT * FROM ndt7_managed UNION ALL
      SELECT * FROM ndt7_autonode
    )
  ),

  result AS ( -- Aggregate all results.
    # This assumes metdata is stable except ignored NULLs
    SELECT
      CONCAT(ContinentCode, '-', CountryCode, '-', site) AS tag,
      server, site, ContinentCode, CountryCode, City, Latitude, Longitude,
      ASNumber, FORMAT('%t, %t', ASNumber, IFNULL(ASName, '(missing)')) AS ASName,
      managed, deployment, Org, type, machineType, zone, loadBalanced, networkTier, externalIP, externalIPv6,
      manual, -- Manual attributes
      firstDate, lastDate, missingDays,
      #  ServerMetadata  -- Was Future proof new metadata fields, but cost complexity later
    FROM (
      SELECT
        server,
        ANY_VALUE(site) AS site,
        ANY_VALUE(ContinentCode) AS ContinentCode,
        ANY_VAlUE(CountryCode) AS CountryCode,
        ANY_VALUE(City) AS City,
        ANY_VALUE(Latitude) AS Latitude,
        ANY_VALUE(Longitude) AS Longitude,
        ANY_VALUE(ASNumber) AS ASNumber,
        ANY_VALUE(ASName) AS ASName,
        ANY_VALUE(managed) AS managed,
        ANY_VALUE(deployment) AS deployment,
        ANY_VALUE(Org) AS Org,
        ANY_VALUE(type) AS type,
        ANY_VALUE(machineType) AS machineType,
        ANY_VALUE(zone) AS zone,
        ANY_VALUE(loadBalanced) AS loadBalanced,
        ANY_VALUE(networkTier) AS networkTier,
        ANY_VALUE(externalIP) AS externalIP,
        ANY_VALUE(externalIPv6) AS externalIPv6,
        ANY_VALUE(ServerMetadata) AS ServerMetadata,
        ANY_VALUE(manual) AS manual,
        MIN(date) AS FirstDate,
        MAX(date) AS LastDate,
        DATE_DIFF(MAX(date), MIN(date),DAY) - COUNT(DISTINCT date) + 1 AS missingDays,
      FROM ndt7_all
      GROUP BY server
    )
  ),

  inventory AS ( 
    SELECT
      # COUNT(*),
      name
    FROM ndt7_all, UNNEST(ServerMetadata)
    GROUP BY name
  )
  # SELECT * FROM inventory -- testcode 
  SELECT  * FROM result ORDER BY tag, server;
