# Climate Pulse

Climate Pulse is an early-stage public web product for tracking climate-related events worldwide. The current build focuses on **Standard Events** from authoritative feeds while reserving a separate **Emerging Signals** editorial layer for less-standardized cryosphere, ecosystem and biodiversity reports.

## Current product baseline

- World map with the prime meridian centered.
- Standard Events from NASA EONET, GDACS and Copernicus CEMS, collected by a GitHub Actions backend three times per day.
- EONET is intentionally used for non-wildfire hazards; GDACS is the primary wildfire source to avoid the known EONET/GDACS global-wildfire duplicate path.
- Major-wildfire rule: GDACS burned area must be **>= 10,000 ha** before it enters the main map.
- Major wildfires are enriched with GHSL 2025 population in the mapped footprint and in a surrounding 5 km buffer.
- Tropical cyclones use GDACS wind/impact polygons when available; remote Green events with no mapped population exposure can be hidden from the main map.
- Nearby major-wildfire records may be grouped for map readability while retaining the underlying source records.
- Event cards expand to show source IDs, update times, coordinates, source links and hazard-specific population-exposure fields.
- A public **Methods & definitions** page documents operational rules and scientific limitations.
- Climate context is explicitly separated from event attribution.

## Scientific reference data

Climate Pulse keeps large authoritative source archives outside Git but can publish compact, reproducible derivatives needed by the website and future Flutter app.

### Population

- JRC GHSL GHS-WUP-POP R2025A, epoch 2025.
- Authoritative ~1 km source is downloaded/cached transiently for event calculations.
- A compact 0.1° population-count derivative is stored under `data/reference/population/`.

### CRU climate context

- Current source: **CRU-TS v4.10**, 1901-2025, 0.5° global land grid except Antarctica.
- Source monthly variables: `tmp` temperature, `pre` precipitation and `vap` actual vapour pressure.
- Monthly VPD is derived as `VPD = SVP(T) - AVP`, with CRU `vap` used as AVP and the piecewise SVP formulation documented on the public methods page.
- Annual `tmp`, `vap` and `vpd` are calendar-day-weighted means; annual `pre` is the sum of monthly totals.
- The publication product is one compressed NetCDF file per year under `data/reference/climate/cru_ts_4.10/annual/`.
- VPD derived from monthly-mean temperature is a coarse climate-context metric and does not retain sub-daily/diurnal temperature variability.

See [`methods.html`](methods.html) for public definitions and limitations.

## Repository layout

```text
index.html
methods.html
assets/
data/
  events/
    latest.json
    lifecycle.json
    archive/
  exposure/
    population/
    landcover/
  reference/
    population/
    climate/
      cru_ts_4.10/
        metadata.json
        README.md
        annual/
          index.json
          cru_ts4.10_YYYY_annual.nc
  derived/impact/
  schemas/
docs/
scripts/
```

## Data strategy

GitHub stores **compact, derived and versioned products**, not full global source archives. Full ERA5/ERA5-Land, monthly CRU source files, high-resolution land-cover rasters and authoritative global population rasters remain in their official services or backend cache. Climate Pulse stores provenance plus reproducible derivatives and per-event results.

The GitHub Pages artifact is intentionally kept lightweight: it contains the website and the current event snapshot, while larger scientific reference files remain publicly accessible through the GitHub repository and `raw.githubusercontent.com`.

The website first checks `data/events/latest.json`. If a non-empty repository snapshot exists, it uses that versioned file. During bootstrap/failure modes it can fall back to direct live APIs.

## Scientific framing

An event appearing in an authoritative hazard feed does not prove anthropogenic climate causation. Long-term local warming/drying context is background information; formal attribution evidence, when available, is a separate field and must be explicitly sourced.

## Status

Prototype / research-and-product exploration.
