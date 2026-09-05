# Population reference

"
        "Climate Pulse uses the European Commission JRC **GHSL GHS-WUP-POP R2025A** "
        "population product as its default population reference.

"
        "## Files committed here

"
        "- `ghsl_wup_2025_0p1deg.tif` — derived 0.1° WGS84 population-count grid for "
        "fast screening, visualization, and approximate proximity checks.
"
        "- `metadata.json` — provenance, processing details, source/derived totals, and hashes.

"
        "## Accuracy rule

"
        "The 0.1° derivative is **not** the preferred layer for final event-exposure statistics. "
        "When a wildfire, flood, cyclone, heat, drought, or landslide footprint is available, "
        "the backend should use the authoritative GHSL 1 km source raster and commit only the "
        "per-event exposure result. This keeps GitHub lightweight while preserving analysis quality.

"
        "## Source

"
        "European Commission Joint Research Centre, GHS-WUP-POP R2025A, epoch 2025. "
        f"DOI: {DATASET_DOI}. Reuse is permitted with proper acknowledgement of the source.
"
        