# WILDFIRE_TRANSPORT_PROSPECTIVE_S5P_CO_INTERIM_V1

Status: pre-outcome analysis contract for the first frozen prospective batch.

This contract is committed before reading any Sentinel-5P CO concentration
outcomes for the batch frozen at
`data/research/wildfire_transport_prospective_v2/frozen/20260925T200833Z.json`.

## Purpose

Compare the already-frozen V4b corridor with the already-locked continuous
transport-score geometry using Sentinel-5P NRTI CO. No model parameter,
prediction geometry, event identity, or exclusion rule may be changed in
response to the outcome.

## Frozen predictions

- V4b: IFS 100 m wind, 12 h forward transport, 50 km corridor.
- Continuous score: sigma 35 km, travel tau 12 h, sqrt-FRP weighting on,
  competing-fire mask off.
- Prediction hashes must verify before any CO values are reduced.

## Observation windows

Two windows are evaluated without choosing between them after seeing outcomes:

1. **Primary temporal alignment:** the exact frozen transport window,
   2026-09-25 21:00 UTC to 2026-09-26 09:00 UTC.
2. **Availability sensitivity:** the UTC day containing the transport-window
   end, 2026-09-26 00:00 to 24:00 UTC.

The prior coverage-only probe was outcome-blind and established that 7/12 events
are coverage-eligible in the exact transport window and 11/12 are eligible in
the end-day window. Those sets are not altered after outcome inspection.

## Coverage eligibility

For each prediction geometry, compare the original downwind shape with copies
rotated +90, 180, and -90 degrees about the reported fire center.

An individual geometry is eligible only when:

- the downwind geometry has at least 50% valid S5P CO coverage; and
- at least two of the three matched control orientations also have at least 50%
  valid coverage.

An event enters the V4b-versus-score comparison only when **both** frozen
geometries satisfy this rule.

## Outcome metric

For each eligible geometry:

`directional_contrast = downwind_CO_mean - mean(eligible_control_CO_means)`

The direct model comparison is:

`transport_minus_v4b = score_directional_contrast - v4b_directional_contrast`

Positive values favor the locked transport-score geometry; negative values
favor V4b for this directional CO contrast.

Report:

- eligible-event count;
- positive-improvement-event count;
- median transport-minus-V4b contrast;
- deterministic bootstrap 95% CI of the event-level median;
- 500 km and 800 km complete-link spatial-block sensitivity, using the median
  event contrast within each block before aggregation;
- per-event contrasts for auditability.

## Governance

This is an **interim, pre-checkpoint diagnostic**, not a success/failure
decision point. The prospective protocol's formal reporting checkpoints remain
30, 50, and 100 eligible fires. No tuning, stopping, event replacement, or
model-selection decision is permitted from this first batch.

S5P CO is the primary satellite outcome. MAIAC AOD remains supportive and will
be evaluated separately when available. This analysis does not validate PM2.5
concentration, dose, affected population, health impact, exact plume boundaries,
or causal attribution of all observed CO to the focal wildfire.
