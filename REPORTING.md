# Climate Pulse reporting pipeline

## Raw collection authority

The event monitor remains at **3 runs per day** (`00:17`, `08:17`, `16:17` UTC). Each run keeps an enriched timestamped snapshot under `data/events/archive/YYYY/MM/DD/HHMMSSZ.json`. Raw population and other numeric metrics are stored at source precision; webpage rounding never changes stored values.

## Daily reporting ledger

After source-first enrichment, footprint QC, asset exposure and climate context are complete, `scripts/update_daily_ledger.py` updates one UTC-day ledger at `data/history/daily/YYYY/MM/DD.json`.

The ledger now stores:

- stable source identity, preferring a GDACS event ID when available;
- exact latest and maximum raw metrics across repeated same-day observations;
- separate temporal fields for `source_updated_at`, `event_start`, `event_end` and `last_detection`;
- lifecycle first/last seen metadata;
- whether the event met the public/significant display rule at least once that day;
- a compact reporting geometry with an explicit geometry semantic grade.

The raw 3x-daily archive remains authoritative and is never replaced by the ledger.

## Geometry semantics

Mapped polygons are not treated as physically interchangeable.

- **Exposure-grade** — wildfire mapped perimeters and tropical-cyclone hazard zones. These may contribute to the cross-hazard mapped-population headline.
- **Context-grade** — GDACS flood reported affected/event areas. These are not observed inundation extent and are excluded from the cross-hazard exposure headline. Population inside them may be reported separately as context.
- **Risk-grade** — drought risk/impact polygons. These are used for drought/crop context and not as direct human-exposure footprints.

This prevents a broad flood event-area polygon from being treated as equivalent to a wildfire perimeter or cyclone wind field.

## Event universes

Reports retain two event universes:

1. **Public/significant events** — a stable event that met the public display rule at least once in the period. Headline report metrics use this universe.
2. **All monitored events** — all stable events retained in the daily ledgers for audit and background statistics.

For screened hazards such as wildfire and tropical cyclone, legacy ledger records without a persisted display decision fail closed and are not promoted to the significant headline universe.

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

Public/significant **exposure-grade** footprints are first deduplicated by stable event identity and then spatially unioned. JRC GHSL 2025 population is extracted from that union, so mapped geographic overlap is counted once.

This is a **Climate Pulse spatial exposure estimate**. It is not verified tracking of unique affected individuals.

### Flood reported-area population context

Public/significant context-grade flood event polygons may be spatially unioned and intersected with GHSL population, but the result is reported separately as population within reported flood event areas. It is not mixed into the cross-hazard exposure headline because the polygon is not observed inundation extent.

### Drought crop area

Public/significant drought risk-grade footprints are spatially unioned before extracting FAO CROPGRIDS 2020 physical crop area. The result is **crop area inside mapped drought risk/impact footprints**, not confirmed crop damage or crop loss.

### Wildfire burned area

The wildfire headline is the **spatial union of mapped wildfire footprints**. This removes mapped overlap across stable wildfire events. The event-deduped sum of source-reported burned-area values remains in JSON as a secondary diagnostic.

### Source/model population sum

Reports retain an event-deduped sum of preferred source/model population metrics as a secondary diagnostic. It counts a stable event once but cannot remove people shared between different events.

## Event lifecycle

Reports classify significant events as:

- **New this period** — first seen during the report period;
- **Ongoing** — first seen before the period and still observed through the available period;
- **Resolved this period** — asserted only when period day coverage is complete and the event is no longer observed by the period end.

If coverage is incomplete, the system deliberately avoids claiming resolution.

Per-event report JSON also stores peak and latest preferred population metrics, period mapped-footprint union area and the additional mapped area accumulated after the event's first observed day in the period.
