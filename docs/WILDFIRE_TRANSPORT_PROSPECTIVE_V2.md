# WILDFIRE_TRANSPORT_PROSPECTIVE_V2

Status: prospective validation protocol; no production exposure semantics.

## Question

Do outcome-blind, wind-informed wildfire screening geometries identify subsequent
satellite-observed transport better than matched non-downwind controls, and does
the already-locked continuous transport score improve on the fixed-width V4b
corridor?

## Frozen models

No parameter tuning is permitted in this experiment.

- V4b baseline: ECMWF IFS 100 m wind, <=12 h forward transport, 50 km corridor.
- Continuous score: the pre-existing V1 lock (sigma 35 km, travel tau 12 h,
  sqrt-FRP weighting on, competing-fire mask off).

## Prospective order

1. Select a current QC-passed wildfire from the public Climate Pulse universe.
2. Before reading validation outcomes, freeze event identity, VIIRS seeds, IFS
   cycle/leads, model definitions and a canonical SHA-256 prediction hash.
3. Persist that freeze in the isolated public `research-data` branch.
4. Only after the relevant observation date is available, evaluate Sentinel-5P
   CO (primary), MAIAC AOD047 (supportive), and AAI (diagnostic).
5. Compare the downwind geometry with same-shape +90/180/-90 degree controls.
6. Aggregate event-level evidence and 500/800 km complete-link spatial blocks.

The prediction hash is checked before validation. A changed prediction is a new
prediction and cannot inherit the old prospective timestamp.

## Accumulation

Report checkpoints at 30, 50 and 100 eligible fires when available. Do not stop
or retune because an intermediate checkpoint is favorable or unfavorable.

## Guardrails

This evaluates directional screening. It does not validate PM2.5 concentration,
dose, affected population, health effects, exact plume boundaries or causal
attribution of all observed aerosol to the focal wildfire.
