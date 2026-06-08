# `cached_metadata` (table)

**BigQuery resource:** `mlab-collaboration:mm_preproduction.cached_metadata`  
**Type:** TABLE

> Auto-generated from the live BigQuery schema by `tools/gen_docs.py`.

## Description

> _Derived from how the dashboard queries it — please review for accuracy._

M-Lab **server/site metadata** for the cached processing stack — one row per
server. The Regional Details dashboard uses it only to populate two dropdowns
(it is not queried by the data panels):

* **Anchor metro** dropdown — groups rows by *metro* (the first three letters of
  `site`) and labels them with `ContinentCode`, `City`, `CountryCode`.
* **Servers** dropdown — computes the great-circle distance
  (`ST_DISTANCE` on `Longitude`/`Latitude`) from the chosen anchor metro to
  every site, then keeps sites within the selected `radius` (km), labelled with
  distance, country, and `ASName`.

Key columns: `site`/`Server` (identity), geographic fields
(`ContinentCode`, `CountryCode`, `City`, `Latitude`, `Longitude`), AS info
(`ASNumber`, `ASName`), deployment metadata, and date coverage
(`FirstDate`, `LastDate`, `MissingDays`).

## Schema

| Column | Type | Mode |
|--------|------|------|
| `tag` | STRING |  |
| `Server` | STRING |  |
| `site` | STRING |  |
| `ContinentCode` | STRING |  |
| `CountryCode` | STRING |  |
| `City` | STRING |  |
| `Latitude` | FLOAT |  |
| `Longitude` | FLOAT |  |
| `ASNumber` | INTEGER |  |
| `ASName` | STRING |  |
| `managed` | STRING |  |
| `deployment` | STRING |  |
| `Org` | STRING |  |
| `type` | STRING |  |
| `machineType` | STRING |  |
| `zone` | STRING |  |
| `loadBalanced` | STRING |  |
| `networkTier` | STRING |  |
| `externalIP` | STRING |  |
| `externalIPv6` | STRING |  |
| `manual` | STRING |  |
| `FirstDate` | DATE |  |
| `LastDate` | DATE |  |
| `MissingDays` | INTEGER |  |
