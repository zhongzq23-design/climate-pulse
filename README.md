# Climate Pulse

Climate Pulse is an early-stage public web product for tracking climate-related events worldwide. The current build focuses on **Standard Events** from authoritative feeds while reserving a separate **Emerging Signals** editorial layer for less-standardized cryosphere, ecosystem and biodiversity reports.

## Current product baseline

- World map with the prime meridian centered.
- Standard Events from NASA EONET, GDACS and Copernicus CEMS, collected by a GitHub Actions backend three times per day.
- EONET is intentionally used for non-wildfire hazards; GDACS is the primary wildfire source to avoid the known EONET/GDACS global-wildfire duplicate path.
- For the public map, Orange and Red GDACS wildfires remain visible. A Green wildfire must have **>= 10,000 ha** burned area and **>= 10,000 people within 5 km**, using current GDACS/GWIS source metrics when available.
- Distinct wildfire source IDs remain separate events; spatial or temporal proximity alone does not merge or cluster nearby fires.
- Wildfire population metrics use GDACS/GWIS first. Climate Pulse uses GHSL only as an explicitly labelled fallback when the equivalent source value is unavailable.
- Tropical cyclones use structured GDACS advisory exposure and mapped hazard geometry when available; fallback exposure is clearly labelled.
- Event cards expand to show source IDs, update times, coordinates, source links, hazard-specific exposure/impact fields, mapped footprints when available and long-term climate context.
- A public **Methods & definitions** page documents operational rules, metric semantics and scientific limitations.
- Climate context is explicitly separated from event attribution.

## Scientific reference data

Climate Pulse keeps large authoritative source archives outside Git but can publish compact, reproducible derivatives needed by the website and future Flutter app.

### Population

- JRC GHSL GHS-WUP-POP R2025A, epoch 2025.
- The authoritative ~1 km source is downloaded/cached transiently for event calculations.
- Compact derived products and per-event results are stored in the repository as needed for reproducibility and reporting.

### CRU climate context

- Current source: **CRU-TS v4.10**, 1901-2025, 0.5° global land grid except Antarctica.
- Source monthly variables: `tmp` temperature, `pre` precipitation and `vap` actual vapour pressure.
- Monthly VPD is derived as `VPD = SVP(T) - AVP`, with CRU `vap` used as AVP and the piecewise SVP formulation documented on the public methods page.
- Annual `tmp`, `vap` and `vpd` are calendar-day-weighted means; annual `pre` is the sum of monthly totals.
- The publication product is one compressed NetCDF file per year under `data/reference/climate/cru_ts_4.10/annual/`.
- VPD derived from monthly-mean temperature is a coarse climate-context metric and does not retain sub-daily/diurnal temperature variability.

See [`methods.html`](methods.html) for public definitions and limitations.

## Public scientific writing governance

Public methods, definitions, explainers, report interpretations and other scientific-description pages follow [`docs/PUBLIC_SCIENCE_WRITING_RULES.md`](docs/PUBLIC_SCIENCE_WRITING_RULES.md).

The policy requires:

- scientific meaning to be preserved before stylistic improvement;
- source-reported, modelled, derived, supplemental and observed quantities to remain distinct;
- GPT/project agents to resolve routine editorial decisions using the current repository source of truth and, when needed, verified authoritative documentation or peer-reviewed research;
- unresolved scientific ambiguity to be expressed neutrally or omitted rather than guessed;
- SCFL (Structure -> Clarity -> Flow -> Language) and final invariant checks before publication;
- no unresolved human-review/editorial placeholders in public scientific copy.

`AGENTS.md` makes this policy binding for future GPT/Codex work. GitHub Pages also runs `scripts/check_public_science_copy.py` before deployment as a deterministic publication guard. This editorial policy does not replace event/data publication rules in `docs/REVIEW_POLICY.md`.

## Repository layout

```text
AGENTS.md
index.html
methods.html
reports.html
REPORTING.md
assets/
data/
  events/
    latest.json
    lifecycle.json
    archive/
  exposure/
    population/
    assets/
  footprints/
  climate/
    event_timeseries/
  history/
    daily/
  reports/
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
  DATA_ARCHITECTURE.md
  PUBLIC_SCIENCE_WRITING_RULES.md
  REVIEW_POLICY.md
scripts/
tests/
```

## Data strategy

GitHub stores **compact, derived and versioned products**, not full global source archives. Full ERA5/ERA5-Land, monthly CRU source files, high-resolution land-cover rasters and authoritative global population rasters remain in their official services or backend cache. Climate Pulse stores provenance plus reproducible derivatives and per-event results.

The GitHub Pages artifact is intentionally kept lightweight: it contains the website and the current browser-facing event products, while larger scientific reference files remain publicly accessible through the GitHub repository and `raw.githubusercontent.com`.

The website first checks `data/events/latest.json`. If a non-empty repository snapshot exists, it uses that versioned file. During bootstrap/failure modes it can fall back to direct live APIs.

## Scientific framing

An event appearing in an authoritative hazard feed does not prove anthropogenic climate causation. Long-term local warming or drying context provides background information; formal attribution evidence, when available, is separate and must be explicitly sourced.

## Status

Prototype / research-and-product exploration.
