# Climate Pulse

Climate Pulse is an early-stage public web product for tracking climate-related events worldwide. The current build focuses on **Standard Events** from authoritative feeds while reserving a separate **Emerging Signals** editorial layer for less-standardized cryosphere, ecosystem and biodiversity reports.

## Current product baseline

- World map with the prime meridian centered.
- Live Standard Events from NASA EONET, GDACS and Copernicus CEMS.
- EONET is intentionally used for non-wildfire hazards; GDACS is the primary wildfire source to avoid the known EONET/GDACS global-wildfire duplicate path.
- Major-wildfire rule: GDACS burned area must be **>= 10,000 ha** before it enters the main map.
- Nearby major-wildfire records may be grouped for map readability while retaining the underlying source records.
- Event cards expand to show source IDs, update times, coordinates and source links.
- Climate context is explicitly separated from event attribution.

## Repository layout

```text
index.html
assets/
  app.js
  sources.js
  styles.css
  world.svg
data/
  events/
    latest.json
    archive/
  climate/
    annual/
    five_year/
    event_timeseries/
  exposure/
    population/
    landcover/
  derived/impact/
  schemas/
docs/
scripts/
```

## Data strategy

GitHub stores **compact, derived, browser-readable products**, not full global source archives.

Suitable repository data include normalized event JSON/GeoJSON, annual or 5-year temperature context, per-event climate time series, population exposure, land-cover composition, natural-vegetation fraction and combined/cumulative-impact indicators.

Full ERA5/ERA5-Land, CRU grids, high-resolution land-cover rasters and global population rasters should remain in their authoritative services or external storage. Climate Pulse stores provenance plus reproducible derived outputs.

The website first checks `data/events/latest.json`. If a non-empty repository snapshot exists, it uses that versioned file. During the bootstrap stage it falls back to direct live APIs.

## Scientific framing

An event appearing in an authoritative hazard feed does not prove anthropogenic climate causation. Long-term local warming context is background information; formal attribution evidence, when available, is a separate field and must be explicitly sourced.

## Status

Prototype / research-and-product exploration.
