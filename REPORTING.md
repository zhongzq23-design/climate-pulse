# Climate Pulse reporting pipeline

## Raw collection authority

The existing event monitor remains unchanged at **3 runs per day** (`00:17`, `08:17`, `16:17` UTC). Each run keeps an enriched timestamped snapshot under `data/events/archive/YYYY/MM/DD/HHMMSSZ.json`. Raw population and other numeric metrics are stored at their source precision; webpage rounding never changes stored values.

## Daily reporting ledger

After source-first enrichment, footprint QC, asset exposure and climate context are complete, `scripts/update_daily_ledger.py` updates one UTC-day ledger at `data/history/daily/YYYY/MM/DD.json`.

The ledger:

- uses stable source identity, preferring a GDACS event ID when available;
- merges repeated observations of the same event within a day while retaining exact latest and maximum raw metrics;
- stores a compact reporting geometry derived from the same-run unsimplified mapped source footprint;
- retains footprint method/QC metadata for later audit;
- never replaces the 3x-daily raw archive.

## Display precision

Population counts are rounded **only in the browser/report presentation layer**:

- `1–999` → `<1,000`
- `>=1,000` → nearest thousand, shown with `≈`
- raw JSON retains the exact upstream/derived number.

## Rolling previews

Every monitoring run updates:

- `Rolling 7 days`
- `Month to date`

These are previews, not frozen publications. They are listed at `reports.html`.

## Scheduled publications

A separate GitHub Actions workflow publishes:

- a **weekly report** every Monday at `06:40 UTC`, covering the previous Monday–Sunday;
- a **monthly report** on the first day of each month at `07:10 UTC`, covering the previous calendar month.

Frozen report JSON is stored under `data/reports/weekly/` and `data/reports/monthly/`; browser reports are stored under `reports/weekly/` and `reports/monthly/`.

## Deduplication and interpretation

### Unique mapped population exposed

For wildfire, storm and flood, reporting footprints are first deduplicated by stable event identity and then spatially unioned. JRC GHSL 2025 population is extracted from the union, so geographic overlap between mapped event footprints is counted once.

This is a **Climate Pulse spatial exposure estimate**. It is not verified tracking of unique individuals and must not be labelled `affected population` unless an upstream source explicitly reports actual affected people.

### Drought crop area

Drought footprints are spatially unioned before extracting FAO CROPGRIDS 2020 physical crop area. The result is **crop area inside mapped drought risk/impact footprints**, not confirmed crop damage or crop loss.

### Source population sum

Reports may also retain an event-deduped sum of preferred source/model population metrics. This counts a stable event once but cannot remove people shared between different events. It is therefore secondary to the spatially deduplicated mapped-population estimate.
