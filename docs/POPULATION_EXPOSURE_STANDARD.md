# Climate Pulse population-exposure standard

**Status:** Canonical Population Exposure Standard v1.2  
**Adopted:** 2026-09-13  
**Scope:** Public population-exposure metrics, periodic-report headlines, and hazard-footprint semantics.

## 1. Core principle

Climate Pulse does **not** use one universal distance radius around every disaster to define an exposed or affected population. The primary population-exposure method is hazard-specific spatial overlay:

> **mapped hazard footprint × gridded resident population**

This follows the disaster-risk meaning of *exposure*: people or assets located in hazard-prone areas. Exposure is not the same as verified harm, displacement, evacuation, casualties, or economic loss.

The public cross-hazard headline therefore uses only geometries that are explicitly classified as **exposure-grade**. A geometry that is only a reported administrative/event area, a proximity buffer, a forecast envelope, an insufficiently verified wildfire polygon, or a risk/context footprint must not silently enter that headline.

## 2. Population reference and spatial deduplication

Climate Pulse currently uses **JRC GHSL GHS-WUP-POP R2025A, epoch 2025, approximately 1 km** for Climate Pulse-derived resident-population overlays.

For a reporting period, all public/significant exposure-grade footprints are spatially unioned before population extraction:

```text
Unique mapped population exposed
= GHSL resident population inside UNION(all eligible exposure-grade footprints)
```

This removes mapped geographic overlap across events and hazards. It does **not** identify or track individual people, and it does not prove that every resident was physically present at the time of the event.

## 3. Evidence grades

### Exposure-grade

A hazard footprint with semantics suitable for direct spatial exposure aggregation. Only this grade is eligible for the cross-hazard population headline.

Current example:

- a GDACS/GWIS wildfire burned-area / fire-perimeter geometry that passes the wildfire admission gate in section 4.

A tropical-cyclone wind polygon is exposure-grade only when the stricter TC requirements in section 4 are satisfied. A generic wind-labelled polygon is not sufficient.

Future source layers may be admitted only after their physical meaning is verified and the classification is encoded explicitly.

### Proximity-grade

A literature- or authority-supported distance buffer around a hazard footprint. Proximity can be useful for evacuation, smoke, access, or near-fire screening, but it is a different quantity from primary footprint exposure.

Climate Pulse may report wildfire proximity metrics such as population within **5 km** of a fire perimeter as a separate secondary metric. A proximity-grade value must never be substituted silently for primary footprint exposure or included in the cross-hazard headline.

### Context-grade

A broad reported event area, administrative/reporting polygon, forecast envelope, or otherwise insufficiently specified hazard geometry that does not establish the required physical exposure semantics.

Current examples:

- GDACS flood reported event/affected-area polygons;
- tropical-cyclone polygons for which wind threshold and observed/actual temporal semantics have not both been explicitly verified;
- wildfire polygons that do not pass the source-provenance, episode-alignment, and area-integrity admission gate.

Population inside such polygons may be shown as **context only** and is excluded from the cross-hazard exposure headline.

### Risk-grade

A hazard risk, potential-impact, or screening footprint that is scientifically useful but does not establish realized human exposure or damage.

Current example:

- mapped drought risk/impact areas used for crop-area context.

Risk-grade geometries do not enter the cross-hazard human-exposure headline unless a later, hazard-specific method defines and validates an exposure-grade threshold layer.

## 4. Hazard-specific rules

### Flood

**Primary standard:** population inside a mapped inundation footprint; no generic distance buffer.

Satellite-observed or otherwise physically explicit inundation extent can support exposure-grade flood population estimates after source-specific quality control. This is consistent with large-scale flood-exposure studies that intersect observed inundation with population, including Tellman et al. (2021).

**Current Climate Pulse implementation:** GDACS flood polygons are treated as **context-grade reported event/affected areas**, not observed inundation. Their population is reported separately as context and excluded from the cross-hazard headline.

Climate Pulse applies a source-feature QC before retaining a flood context polygon: it selects one GDACS polygon feature aligned to the reported event coordinate and suppresses the footprint when no source feature lies within 75 km. This QC only checks that the selected context feature is plausibly associated with the event. It **does not establish inundation** and never upgrades the polygon to exposure-grade. Very large reported event areas are explicitly warned as context so area size cannot be mistaken for flooded land area.

A future flood layer may be promoted to exposure-grade only when all of the following are true:

1. the source explicitly represents inundation or an equivalent physically defined flood hazard footprint;
2. source-specific spatial quality control passes;
3. the geometry semantics are persisted in the daily ledger;
4. regression tests verify that reported-area context cannot be mistaken for inundation.

### Wildfire — strict v1.2 admission rule

**Primary standard:** resident population inside a mapped burned-area / wildfire perimeter.

GDACS forest-fire events are supplied by the Global Wildfire Information System (GWIS). GDACS documents the GWIS near-real-time burned-area product as defining individual wildfire perimeters from MODIS/VIIRS active-fire information. Climate Pulse therefore permits an episode-aligned GWIS perimeter to become exposure-grade, but the event type alone is no longer sufficient evidence.

For a mapped wildfire polygon to be **exposure-grade**, all of the following must pass:

1. the source burned-area metric has explicit **GDACS/GWIS** provenance;
2. the burned-area metric and mapped footprint refer to the **same GDACS/GWIS episode**;
3. a valid source-reported burned-area value is available;
4. the mapped polygon area is broadly consistent with that source-reported burned area;
5. the admission result is persisted in `geometry_qc` and the daily ledger.

The current operational area-integrity gate requires:

```text
0.5 <= mapped polygon area / source burned area <= 2.0
```

This deliberately broad factor-of-two bound is a **coarse corruption/semantic integrity guard, not a scientific uncertainty interval**. A relative difference greater than 25% is retained as a warning even when the footprint remains within the admission bound. The purpose is to catch gross source/episode/geometry mismatches without pretending that the comparison defines measurement uncertainty.

If provenance is missing, episodes do not match, or the area ratio fails the coarse integrity gate, the wildfire geometry is retained only as **context-grade** and cannot contribute to `Unique mapped population exposed`.

This perimeter-based primary exposure follows the same basic spatial concept used by Modaresi Rad et al. (2023), who define primary human exposure as population residing within large-wildfire perimeters.

**Secondary proximity standard:** near-fire population may be reported separately. OECD (2022) provides a documented methodology using a **5 km buffer** around monthly wildfire perimeters before population overlay. Climate Pulse therefore permits a 5 km wildfire proximity metric as a secondary, explicitly labelled quantity.

The 5 km metric is **not** part of the primary cross-hazard population headline.

### Tropical cyclone / storm — strict v1.1 rule retained in v1.2

GDACS documents tropical-cyclone population exposure using wind buffers associated with **34 kt** and **64 kt** winds. Source advisory population values are retained as source/modelled event-level exposure metrics, but they are not automatically equivalent to a spatially deduplicated Climate Pulse headline.

The GDACS timeline payload currently exposes raw fields named `pop39` and `pop74`. Climate Pulse does **not** interpret the numeric suffixes of those raw field names as 39-kt and 74-kt scientific thresholds. The public methodological interpretation follows the documented GDACS 34-kt and 64-kt wind-buffer method. Legacy machine-readable Climate Pulse keys containing `39kt` or `74kt` may remain in archived data for backward compatibility and must not be read literally as the physical wind thresholds.

For a mapped tropical-cyclone polygon to be **exposure-grade**, all of the following must be explicit and machine-verifiable:

1. the geometry is a wind polygon rather than a generic event area, centre buffer, storm-surge area, or mixed impact polygon;
2. the wind threshold is explicitly identified as a supported primary threshold, currently **34 kt** or **64 kt**;
3. the temporal semantics are explicitly **observed/actual**, not merely forecast, projected, generic, current-but-forecast, or unspecified;
4. the geometry method persisted in the daily ledger preserves those semantics;
5. regression tests prove that generic, forecast, fallback, and unverified TC geometry fails closed.

A method label such as `gdacs_wind_polygon` by itself does **not** satisfy these requirements. The word `wind` is not enough evidence to infer threshold or observation status.

**Current Climate Pulse implementation under v1.2:** the present GDACS polygon selector can identify wind-labelled geometry, but it does not yet prove both the exact 34/64-kt threshold and observed/actual status for the mapped polygon. Therefore current generic `gdacs_wind_polygon` records are **context-grade** and are excluded from the cross-hazard `Unique mapped population exposed` headline. They can still be shown as cyclone map/context geometry, and structured GDACS advisory population metrics remain available separately.

If a later extractor can prove an observed 34-kt or 64-kt wind polygon, it may emit an explicit method such as `gdacs_observed_wind_34kt_polygon` or `gdacs_observed_wind_64kt_polygon`; only then may that geometry enter the headline.

Point-only or broad proximity screening is never promoted to primary TC exposure. EONET-only centre buffers and generic GDACS event-polygon fallbacks remain screening/context only.

### Drought

Drought is not represented by an arbitrary circular buffer. Current GDACS/GDO drought polygons are retained as **risk-grade** and are used for risk/crop context rather than as direct human-exposure footprints.

If Climate Pulse later adds a human drought-exposure metric, it must use an explicit, documented hazard threshold or risk definition applied to a raster or polygon layer, then intersect that layer with population. The threshold, time scale, and interpretation must be published with the metric.

### Heat

Heat exposure should likewise use a physically defined threshold layer (for example a temperature or thermal-stress threshold over a specified time window) crossed with population. A fixed event-centre radius is not the primary method.

Climate Pulse does not currently include heat in the cross-hazard population headline unless an exposure-grade heat footprint has been implemented and validated.

## 5. Exposure is not affected population

Climate Pulse uses these terms distinctly:

- **Exposed population** — resident population spatially co-located with a defined hazard footprint or, when explicitly labelled, a proximity zone.
- **Affected population** — used only for source-reported impact counts; Climate Pulse does not infer this label from a polygon overlay.
- **Displaced / evacuated / injured / fatalities** — used only when supplied by a reliable upstream impact source.
- **Context population** — population located inside a reported administrative/event area or insufficiently verified mapped hazard area whose physical exposure semantics are not established.

EM-DAT likewise distinguishes reported affected-person variables from spatial exposure, and notes that definitions of “affected persons” vary among sources. This is one reason Climate Pulse does not relabel spatial exposure as affected population.

## 6. Periodic-report headline rule

The headline **Unique mapped population exposed** is valid only when all of the following are satisfied:

1. the event belongs to the public/significant reporting universe;
2. a usable mapped geometry exists;
3. the geometry is explicitly `exposure_grade` under the current hazard-specific policy;
4. the population overlay uses the configured authoritative population reference;
5. same-period eligible geometries are spatially unioned before extraction;
6. context-grade, proximity-grade, and risk-grade footprints are excluded.

For wildfires, item 3 requires the v1.2 source-provenance, same-episode, and area-integrity admission gate. For tropical cyclones, it requires an explicit supported wind threshold plus observed/actual temporal semantics. Generic or insufficiently verified geometry fails closed.

The public label should remain explicit, for example:

> **Unique mapped population exposed · exposure-grade**

and its interpretation should state that it is mapped exposure, not verified unique affected individuals.

## 7. Fail-closed rules

- Do not invent a circular footprint around a news point merely to produce a population number.
- Do not promote a broad administrative or event-area polygon to exposure-grade because it is the only geometry available.
- Do not treat a proximity buffer as equivalent to a hazard footprint.
- Do not treat the hazard type `Wildfire` alone as proof that a polygon is an episode-aligned burned-area perimeter.
- Do not treat the presence of the word `wind` in TC geometry metadata as proof of a validated wind threshold.
- Do not treat a forecast TC wind envelope as observed exposure.
- Do not infer 34-kt or 64-kt polygon semantics when the source geometry does not explicitly establish them.
- Do not reinterpret a center-aligned GDACS flood context polygon as inundation.
- Do not silently convert risk/context metrics into affected-population claims.
- If geometry semantics are ambiguous, keep the metric out of the cross-hazard headline.
- If no suitable footprint exists, withhold the derived primary-exposure metric rather than fabricate one.

## 8. References and methodological anchors

- **UNDRR (2017), Sendai Framework terminology — Exposure.** Exposure is the situation of people, infrastructure, housing, production capacities and other tangible human assets located in hazard-prone areas. https://www.undrr.org/terminology/exposure
- **Tellman, B. et al. (2021).** Satellite imaging reveals increased proportion of population exposed to floods. *Nature* 596, 80–86. https://doi.org/10.1038/s41586-021-03695-w
- **Modaresi Rad, A. et al. (2023).** Human and infrastructure exposure to large wildfires in the United States. *Nature Sustainability* 6, 1343–1351. https://doi.org/10.1038/s41893-023-01163-z
- **OECD (2022), Regions and Cities at a Glance — wildfire exposure methodology.** Population exposure to wildfire is calculated from merged fire perimeters with a 5 km buffer and population overlay. https://www.oecd.org/en/publications/oecd-regions-and-cities-at-a-glance-2022_14108660-en/full-report/component-36.html
- **GDACS Forest Fires methodology.** GDACS forest-fire events are supplied by GWIS; the near-real-time burned-area product uses MODIS/VIIRS active-fire information to define individual wildfire perimeters and estimate burned area. https://www.gdacs.org/Knowledge/models_WF.aspx
- **GDACS MHEWS Guide (2025).** Documents both the GWIS wildfire methodology and tropical-cyclone exposure within 34-kt and 64-kt wind buffers. https://www.gdacs.org/documents/2025/GDACS_MHEWS_guide.pdf
- **EM-DAT Documentation — Human Impact Variables.** `Total Affected` combines reported injured, affected and homeless counts; the documentation also notes conceptual variation in “affected persons” among sources. https://doc.emdat.be/docs/data-structure-and-content/impact-variables/human/
- **JRC GHSL GHS-WUP-POP R2025A.** Climate Pulse population reference for derived overlays. https://human-settlement.emergency.copernicus.eu/ghs_wup_pop_r2025a.php

## 9. Change control

This document is the canonical population-exposure policy for Climate Pulse. Scientific-semantic changes to population headline eligibility, evidence grades, or hazard-specific footprint rules must update this file, the public Methods page, reporting documentation, and relevant regression tests in the same change set.

A change in data availability alone must not silently change the meaning of a public metric.
