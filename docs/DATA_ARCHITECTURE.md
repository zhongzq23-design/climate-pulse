# Data architecture

Climate Pulse separates large authoritative source datasets from compact, versioned publication products.

1. **Source layer** — authoritative external APIs and large source datasets.
2. **Publication layer** — normalized event records, compact scientific reference derivatives, and per-event exposure/context products committed to this repository.

Current flow:

```text
EONET / GDACS / CEMS
        ↓
3× daily monitoring
        ↓
normalization + source-aware dedupe
        ↓
hazard-specific display rules
        ↓
GHSL population exposure
        ↓
approved/current event snapshot
        ↓
website + future Flutter app
```

Climate-reference flow:

```text
CRU-TS v4.10 monthly 0.5° source files (external)
        ↓
TMP + PRE + VAP
        ↓
monthly VPD = SVP(Tmonthly) - AVP
        ↓
annual aggregation
        ↓
one compressed NetCDF per year, 1901-2025
        ↓
data/reference/climate/cru_ts_4.10/annual/
        ↓
future event-location climate context / trends / 5-year summaries
```

## Repository layout

```text
data/
├── events/
│   ├── latest.json
│   ├── lifecycle.json
│   └── archive/
├── exposure/
│   └── population/
├── reference/
│   ├── population/
│   │   └── ghsl_wup_2025_0p1deg.tif
│   └── climate/
│       └── cru_ts_4.10/
│           ├── metadata.json
│           ├── README.md
│           └── annual/
│               ├── index.json
│               └── cru_ts4.10_YYYY_annual.nc
└── derived/
    └── impact/
```

Large authoritative source archives are not committed to Git. For GHSL, the authoritative ~1 km population raster is downloaded/cached transiently by the backend and only compact reference data plus per-event results are committed. For CRU-TS, the authoritative monthly files remain at the Climatic Research Unit; Climate Pulse publishes the annual derivative needed for fast event-location context.

The GitHub Pages deployment deliberately excludes the large scientific reference directory. These files remain public through the GitHub repository / `raw.githubusercontent.com`, while the website deployment stays lightweight.

All derived scientific products should carry source version, units, aggregation method, provenance, limitations, and references in machine-readable metadata. Browser-facing products should remain versioned and reproducible.
