# WILDFIRE_TRANSPORT_SCORE_V1

Status: **experimental only; not production semantics**.

This experiment asks whether the existing binary wildfire downwind screen can be made more spatially informative without increasing its area budget.

## Frozen baseline

The baseline is the current V4b family:

- focal source: fire-specific VIIRS detections associated with the QC-passed wildfire footprint;
- wind: ECMWF IFS 100 m;
- forward transport: capped at 12 h;
- geometry: 50 km fixed buffer around forward trajectories;
- interpretation: directional screening corridor only, not a smoke concentration or exposure footprint.

For the 45-fire development stage, each event reuses the IFS `run_creation_time` stored in the authoritative V4 grid artifact rather than substituting a newer weather cycle.

## Continuous transport score

For a candidate grid location `x`, the score is proportional to

`sum(seed, lead) source_weight × exp(-travel_h / tau) × exp(-distance(x, trajectory_node)^2 / (2 sigma^2))`.

Source weight always includes a fixed acquisition-recency decay with a 36 h e-folding time. The experimental grid tests whether adding square-root FRP weighting improves skill.

The predeclared parameter grid is:

- cross-trajectory Gaussian scale `sigma`: 20 or 35 km;
- travel-time e-folding `tau`: 6 or 12 h;
- FRP weighting: off or on;
- competing-fire mask: off or on.

This is 16 configurations. No satellite outcome is used to choose among them.

## Competing-fire masking

For each focal fire, up to 30 strong/recent non-focal VIIRS detections within 500 km are selected after excluding detections inside the focal footprint +20 km. They are advected with the same frozen IFS 100 m cycle and lead sequence. The mask-on variants remove candidate score cells intersecting their 50 km transport corridors.

The mask is a confounder-control experiment, not a claim that all masked aerosol originates from those competing fires.

## Equal-area rule

Every transport-score configuration is converted to a top-score region and iteratively scaled until its geodesic area matches the event-specific V4b baseline area. Therefore a score configuration cannot improve simply by covering more land.

The rasterization grid is 5 km. Area-match error is persisted for every event/configuration and summarized across held spatial blocks.

## 45-fire development and spatial-block selection

The development universe is exactly the frozen 45-fire V4 universe with the existing deterministic 500 km complete-link spatial blocks.

Evaluation uses leave-one-spatial-block-out selection. In each outer fold:

1. the held spatial block is removed;
2. the predeclared V3/V4 spatiotemporal embargo is applied to training fires;
3. each candidate configuration is summarized within training blocks first, so large blocks cannot dominate by event count;
4. configurations with at least 90% block support are eligible;
5. the selected configuration maximizes training-block median CAMS PM2.5 CSI80, with precision80 and PM2.5 enrichment as tie-breakers;
6. the selected score and the equal-area V4b baseline are compared only on the held block.

CAMS is semi-independent because its composition system uses fire-emission information. This stage is model development, not final independent validation.

A single configuration is then locked for satellite validation using the modal outer-fold choice. A tie is resolved by full-development block-median CSI80 and then precision80.

## 24-fire independent satellite gate

The locked configuration is evaluated on the already frozen 24-fire Satellite Validation V2 sample without retuning.

The frozen event selection, focal VIIRS seeds, 100 m trajectories, target date and observation reference time are reused. The transport-score region is equal-area matched to the frozen V4b downwind corridor for each event.

Validation products remain:

- Sentinel-5P CO — primary independent directional tracer;
- MODIS MAIAC AOD047 — supportive aerosol evidence when coverage is adequate;
- Sentinel-5P AAI — diagnostic only because the previous 24-fire validation did not show aggregate directional support.

For each geometry, the downwind region is compared with same-shape rotations at +90°, 180° and -90°. The principal new paired statistic is:

`transport-score directional contrast - V4b directional contrast`.

Results are summarized at event level and again after 500 km and 800 km complete-link spatial blocking.

## Scientific guardrails

A positive result can support only the statement that a continuous, source-weighted transport ranking improves directional screening relative to the equal-area fixed-width V4b corridor.

It does **not** validate:

- PM2.5 concentration;
- exposure dose;
- affected population;
- health impact;
- exact smoke-plume boundaries;
- causal attribution of all observed aerosol to the focal wildfire.

No output from this experiment is written into production event JSON, population exposure, public methods, or the website unless a later explicit production decision is made.
