# Data architecture

Climate Pulse uses two layers:

1. **Source layer** — authoritative external APIs and large source datasets.
2. **Publication layer** — compact normalized/derived JSON, GeoJSON or CSV committed to this repository.

Planned flow:

```text
EONET / GDACS / CEMS
        ↓
normalization + source-aware dedupe
        ↓
review candidates
        ↓
approved event snapshot
        ↓
climate context + population + land cover
        ↓
combined/cumulative impact products
        ↓
website + future Flutter app
```

Large source rasters remain outside Git. Browser-facing products must be small, versioned and reproducible.
