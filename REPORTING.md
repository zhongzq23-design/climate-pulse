# Climate Pulse reporting pipeline

The canonical scientific-semantic policy for population reporting is [`docs/POPULATION_EXPOSURE_STANDARD.md`](docs/POPULATION_EXPOSURE_STANDARD.md). Report-generation code and public copy must remain consistent with that policy. Population Exposure Standard **v1.2** adds an explicit wildfire admission gate while retaining the strict tropical-cyclone v1.1 rule.

## Raw collection authority

The event monitor remains at **3 runs per day** (`00:17`, `08:17`, `16:17` UTC). Each run keeps an enriched timestamped snapshot under `data/events/archive/YYYY/MM/DD/HHMMSSZ.json`. Raw population and other numeric metrics are stored at source precision; webpage rounding never changes stored values.

## Daily reporting ledger

After source-first enrichment, footprint QC, asset exposure and climate context are complete, `scripts/update_daily_ledger.py` updates one UTC-day ledger at `data/history/daily/YYYY/MM/DD.json`.

The ledger stores:

- stable source identity, preferring a GDACS event ID when available;
- exact latest and maximum raw metrics across repeated same-day observations;
- separate temporal fields for `source_updated_at`, `event_start`, `event_end` and `last_detection`;
- lifecycle first/last seen metadata;
- whether the event met the public/significant display rule at least once that day;
- a compact reporting geometry with an explicit geometry semantic grade and source-specific QC.

The raw 3x-daily archive remains authoritative and is never replaced by the ledger.

After the ledger update, `scripts/enforce_population_exposure_policy.py` re-evaluates persisted geometry semantics before any rolling report is built. This is a fail-closed integrity gate: historical or newly written records cannot retain an obsolete permissive geometry grade. For historical wildfire records, the gate reconstructs admission QC from the stored geometry, same-day burned-area metric and burned-area provenance when possible.

## Geometry semantics

Mapped polygons are not treated as physically interchangeable. Climate Pulse does not use a single universal distance radius around all hazards to manufacture an exposed-population footprint.

- **Exposure-grade** — a physically meaningful hazard footprint suitable for direct population overlay. A wildfire polygon qualifies only after the source-provenance, same-episode and coarse area-integrity gate passes. A tropical-cyclone polygon qualifies only when the wind threshold and observed/actual temporal semantics are both explicit and machine-verifiable. Only exposure-grade geometry can contribute to the cross-hazard mapped-population headline.
- **Proximity-grade** — a documented distance buffer around a hazard footprint. For wildfire, a 5 km near-fire population metric is permitted as a separate secondary metric, consistent with an OECD methodology. Proximity-grade values are not merged into the primary cross-hazard exposure headline.
- **Context-grade** — GDACS flood reported affected/event areas, wildfire geometry that fails admission QC, current generic or insufficiently specified tropical-cyclone wind/event polygons, and other broad reporting polygons whose required physical hazard semantics are not established. These are excluded from the cross-hazard exposure headline. Population inside them may be reported separately as context.
- **Risk-grade** — drought risk/impact polygons. These are used for drought/crop context and not as direct human-exposure footprints.

A future flood footprint can enter the exposure headline only when the source explicitly represents inundation or an equivalent physically defined flood-hazard footprint, passes source-specific QC, and is persisted as exposure-grade. A reported flood event area must never be promoted merely because no better geometry is available.

### Strict wildfire geometry gate

GDACS documents forest-fire events as being supplied by the Global Wildfire Information System (GWIS), whose near-real-time burned-area product defines individual wildfire perimeters from MODIS/VIIRS active-fire information. Climate Pulse therefore admits a wildfire polygon to exposure-grade only when the evidence chain is explicit:

1. the source burned-area metric has GDACS/GWIS provenance;
2. the burned-area metric and footprint refer to the same GDACS/GWIS episode;
3. a valid source-reported burned-area value is present;
4. the mapped/source burned-area ratio satisfies the coarse integrity rule `0.5 <= ratio <= 2.0`;
5. the result is persisted in `geometry_qc`.

A relative area difference greater than 25% is retained as a warning even when the footprint still passes. The factor-of-two admission range is a **coarse corruption/semantic guard, not an uncertainty interval**. If any required evidence is missing or the gross area-consistency check fails, the wildfire polygon remains context-grade and is withheld from `Unique mapped population exposed`.

### Strict tropical-cyclone geometry gate

GDACS documents population exposure for tropical cyclones using **34-kt and 64-kt wind buffers**. Climate Pulse keeps structured GDACS advisory population metrics as source/modelled event-level exposure, but a mapped TC polygon can enter the spatially deduplicated cross-hazard headline only when all of the following are explicit:

1. it is a wind polygon rather than a generic event, proximity, storm-surge, or mixed impact area;
2. the mapped wind threshold is identified as 34 kt or 64 kt;
3. the geometry is observed/actual rather than forecast or temporally unspecified;
4. those semantics are persisted in the footprint method and daily ledger.

The generic method label `gdacs_wind_polygon` does **not** pass this gate because it establishes neither the exact threshold nor observed/actual status. Likewise, forecast wind polygons, generic event-polygon fallbacks and centre-distance screening are context/screening only.

The raw GDACS advisory fields `pop39` and `pop74` are not described publicly as literal 39-kt and 74-kt thresholds. The GDACS methods guide describes the scientific wind-buffer exposure method at 34 kt and 64 kt. Historical Climate Pulse JSON may retain legacy key names containing `39kt` or `74kt` for compatibility; those key suffixes must not be interpreted as the physical threshold.

### Flood context selection QC

The current GDACS flood geometry remains **reported event/affected-area context**, not inundation. Climate Pulse selects one source feature aligned to the reported event coordinate and suppresses the footprint when no candidate lies within 75 km. This avoids blindly unioning unrelated polygon resources.

Passing this spatial QC only means the selected source feature is plausibly associated with the event. It **does not establish inundation** and does not upgrade the geometry to exposure-grade. Very large selected areas receive an explicit context warning so reported event-area size cannot be mistaken for flooded land area.

## Event universes

Reports retain two event universes:

1. **Public/significant events** — a stable event that met the public display rule at least once in the period. Headline report metrics use this universe.
2. **All monitored events** — all stable events retained in the daily ledgers for audit and background statistics.

For screened hazards such as wildfire and tropical cyclone, legacy ledger records without a persisted display decision fail closed and are not promoted to the significant headline universe.

### Wildfire event identity

Distinct wildfire source IDs remain distinct stable events. Spatial/temporal proximity alone never merges wildfire records and the public map does not create regional wildfire cluster markers. An explicit source identity/link may still deduplicate two records that demonstrably refer to the same wildfire.

## Display precision

Population counts are rounded **only in the browser/report presentation layer**:

- `1–999` → `<1,000`
- `>=1,000` → nearest thousand, shown with `≈`
- raw JSON retains the exact upstream/derived number.

## Coverage integrity

Every report stores the number of requested UTC days, days with a daily ledger, missing dates and represented monitor runs.

- Rolling 7-day and month-to-date products may be generated with incomplete coverage, but they are prominently labelled **Partial coverage** and their headline values are provisional.
- A frozen weekly or monthly publication is indexed as an official report **only when every requested UTC day has a ledger**.
- If a frozen period is incomplete, the candidate is written under `data/reports/withheld/` and `reports/withheld/` for audit but is not added to the published weekly/monthly index.

The nominal schedule is three monitor runs per UTC day; run coverage is reported separately from day coverage.

## Rolling previews

Every monitoring run updates:

- `Rolling 7 days`
- `Month to date`

These are previews, not frozen publications. They are listed at `reports.html` with a coverage badge.

## Scheduled publications

A separate GitHub Actions workflow attempts to publish:

- a **weekly report** every Monday at `06:40 UTC`, covering the previous Monday–Sunday;
- a **monthly report** on the first day of each month at `07:10 UTC`, covering the previous calendar month.

Only complete periods enter the published report index.

## Spatial deduplication and headline metrics

### Unique mapped population exposed

Public/significant **exposure-grade** footprints are spatially unioned before JRC GHSL 2025 population is extracted:

```text
unique mapped population exposed
= GHSL population inside UNION(all eligible exposure-grade footprints)
```

Mapped geographic overlap is therefore counted once. Wildfire geometry that fails the v1.2 admission gate, context-grade flood event areas, unverified or forecast tropical-cyclone geometry, proximity-grade buffers and risk-grade drought polygons are excluded.

This is a **Climate Pulse spatial exposure estimate**. It is not verified tracking of unique affected individuals, and gridded resident population does not prove that every resident was physically present during an event.

### Tropical-cyclone source/model exposure

Structured GDACS advisory population values remain useful event-level modelled exposure indicators and can be shown on individual event cards or in secondary diagnostics. They are not spatially unioned across events because the source values are counts, not a common geometry layer. They therefore remain separate from `Unique mapped population exposed` unless a corresponding validated exposure-grade geometry exists.

### Wildfire primary and proximity population

The primary wildfire spatial-exposure definition is population inside a **QC-admitted, episode-aligned GDACS/GWIS burned-area / fire perimeter**. A population-within-5-km metric may be retained as a separately labelled near-fire proximity measure or display-screening variable. It must not replace primary perimeter exposure in the cross-hazard headline.

### Flood reported-area population context

Public/significant context-grade flood event polygons may be spatially unioned and intersected with GHSL population, but the result is reported separately as population within reported flood event areas. It is not mixed into the cross-hazard exposure headline because the current GDACS polygon is not observed inundation extent. Center-alignment QC reduces source-feature mismatch but does not change that scientific interpretation.

If a future source supplies a validated inundation footprint, population inside that inundation footprint may be reported as exposure-grade without adding a generic distance buffer.

### Drought crop area

Public/significant drought risk-grade footprints are spatially unioned before extracting FAO CROPGRIDS 2020 physical crop area. The result is **crop area inside mapped drought risk/impact footprints**, not confirmed crop damage or crop loss.

A future drought human-exposure metric must define a documented hazard threshold/time scale and intersect the resulting threshold footprint with population; an arbitrary event-centre radius is not acceptable.

### Wildfire burned area

The wildfire burned-area headline remains the **spatial union of mapped wildfire footprints** used for wildfire area reporting. The population headline is stricter: only wildfire geometries that pass the v1.2 admission gate can contribute resident population. The event-deduped sum of source-reported burned-area values remains in JSON as a secondary diagnostic.

### Source/model population sum

Reports retain an event-deduped sum of preferred source/model population metrics as a secondary diagnostic. It counts a stable event once but cannot remove people shared between different events and can mix source semantics across hazards. It is therefore not the cross-hazard exposure headline.

## Event lifecycle

Reports classify significant events as:

- **New this period** — first seen during the report period;
- **Ongoing** — first seen before the period and still observed through the available period;
- **Resolved this period** — asserted only when period day coverage is complete and the event is no longer observed by the period end.

If coverage is incomplete, the system deliberately avoids claiming resolution.

Per-event report JSON also stores peak and latest preferred population metrics, period mapped-footprint union area and the additional mapped area accumulated after the event's first observed day in the period.

## Methodological anchors

The exposure policy is grounded in the UNDRR definition of exposure and hazard-specific examples in the scientific and institutional literature, including Tellman et al. (2021) for population within satellite-observed flood inundation, Modaresi Rad et al. (2023) for primary wildfire exposure within fire perimeters, OECD (2022) for a separate 5 km wildfire-proximity definition, GDACS/GWIS documentation for near-real-time wildfire perimeters, and the GDACS MHEWS guide for tropical-cyclone exposure within 34-kt and 64-kt wind buffers. EM-DAT human-impact terminology is used as an additional guard against conflating spatial exposure with source-reported affected people.

Full references and URLs are maintained in [`docs/POPULATION_EXPOSURE_STANDARD.md`](docs/POPULATION_EXPOSURE_STANDARD.md) and on the public Methods page.
