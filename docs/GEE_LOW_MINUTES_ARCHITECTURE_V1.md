# GEE_LOW_MINUTES_ARCHITECTURE_V1

## Goal

Reduce private GitHub Actions minutes without changing the scientific meaning of
Climate Pulse products. GitHub should orchestrate and publish; Earth Engine
should perform raster sampling/reduction; Python should retain trajectory,
geometry, statistics and policy logic that is clearer or safer outside GEE.

## Production refresh

The scheduled production monitor remains at 00:17, 08:17 and 16:17 UTC, but it
no longer runs the full regression/JavaScript test suite. CI remains the job of
`backend-validation.yml`.

The scheduled monitor performs the lightweight source discovery first, including
the 8-21 day wildfire recovery bands, then uses a cheap gate to choose only the
layers that need work:

* `full_refresh`: the source-event fingerprint changed. Run hazard exposure,
  wildfire episode alignment, footprint/asset enrichment, climate context and
  downstream maintenance.
* `climate_refresh`: the source state is unchanged but a new fully elapsed ECMWF
  IFS 0-24 h cycle is available. Run only the climate-context chain plus final
  publication; do not reload GHSL or rerun hazard footprints.
* `maintenance_refresh`: a new UTC day started. Maintain the daily ledger and
  rolling reports without forcing population/footprint calculations.

Manual dispatch forces all layers. If the small GEE metadata probe fails, the
climate layer fails safe to refresh; unrelated population and footprint work is
not repeated.

When the source-event fingerprint is unchanged, the temporary event polling
writes are reset before climate-only or maintenance-only work. Thus lifecycle
counters, generated timestamps and archive snapshots do not create a data commit
merely because the monitor checked for new events.

The source fingerprint ignores enrichment-only fields and ordering while keeping
source identity, hazard metrics, coordinates and dates. This prevents derived
products from recursively forcing another full enrichment cycle.

CRU 1901-1930 and 1981-2010 monthly climatologies are treated as committed,
versioned reference products. The expensive early-normal build remains validated
by CI rather than being recomputed three times per day.

## Timestamp-only product stabilization

Climate context is still recomputed whenever its scientific inputs may have
changed. After computation, `stabilize_climate_products.py` compares each JSON
product with the tracked version at `HEAD`. If the parsed documents differ only
in top-level `generated_at`, the exact tracked bytes are restored. Any value,
window, coverage, event-universe or provenance change is retained.

This specifically prevents large Git commits where dozens of climate files have
identical science but new execution timestamps or JSON key ordering.

## CI and publishing

Generated data-only pushes are excluded from `backend-validation.yml`; PRs and
code/config pushes still receive the full regression/scientific validation.

Static UI/document changes use `publish-public-static.yml` and do not invoke GEE.
`publish-climate-context-now.yml` is reserved for changes to climate-context
calculation code. The scheduled monitor is no longer triggered by ordinary
pushes to main.

Workflow-file-only edits do not self-trigger either public publisher. This
prevents one maintenance commit from starting backend validation, a GEE publish,
a static publish and the full production monitor simultaneously.

## GEE server-side rule

Prefer one `FeatureCollection` and `sampleRegions`/`reduceRegions` call over a
Python loop that calls `reduceRegion` repeatedly. Client-side `getInfo()` is kept
only at compact synchronization boundaries where the returned object is a small
table or metadata record.

For large future products, prefer asynchronous Earth Engine exports rather than
holding a GitHub runner open while GEE computes.

## Deliberately unchanged in V1

* CRU-TS v4.10 historical climate definitions remain unchanged.
* ECMWF IFS recent-context definitions remain unchanged.
* GHSL GHS-WUP-POP R2025A remains the population authority; moving that exact
  raster to a private EE asset is a later phase.
* Transport-score geometry, LOSO selection, bootstrap statistics and spatial
  block logic remain Python-side.
* OpenAQ and source APIs remain external-API work rather than GEE work.

## Wildfire research execution

The experimental wildfire branch has a companion low-minutes architecture that
collapses the former 45-fire and 24-fire matrices to two long-lived jobs. It also
batches the four matched satellite orientations into Earth Engine
`reduceRegions` calls. Scientific selection, locking and independent-validation
guardrails are unchanged.

## Next phase

1. Upload the exact production GHSL R2025A raster as a private EE asset and move
   polygon population sums to `reduceRegions` after parity tests pass.
2. Extend cross-event wind sampling batches where doing so preserves frozen IFS
   timing and trajectory semantics.
3. Use asynchronous GEE export only for products too large for compact table
   retrieval.
