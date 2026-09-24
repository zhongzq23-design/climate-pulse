# Climate Pulse operational lessons

This file records concrete failure modes discovered in production or repository operation and the rules adopted to prevent recurrence. It is a living operational-safety document, not a changelog.

Repository agents and maintainers must read this file before changing automation, GitHub Actions workflows, data-publication logic, documentation-sync scripts, or other code that can block scheduled Climate Pulse updates.

## How to use this file

For every material operational failure, add a short entry containing:

1. **Incident** — what users or maintainers observed.
2. **Impact** — what stopped working or became stale.
3. **Root cause** — the technical reason, not only the visible symptom.
4. **Unsafe pattern** — the design choice that allowed the failure.
5. **Corrective rule** — the reusable rule that future work must follow.
6. **Verification** — how a future change should prove that the same class of failure cannot recur.

Do not remove an old lesson merely because the immediate bug was fixed. Update a lesson only when the architecture has changed enough that the old rule is no longer applicable, and explain why.

---

## 2026-09-11 — Documentation sync blocked the climate-event publication pipeline

### Incident

The public Climate Pulse GitHub Pages site remained reachable, but the event view became stale and could appear empty. The scheduled `Monitor climate events` workflow failed near the end of an otherwise successful run.

The failing step was:

`Sync wildfire identity policy documentation`

The script raised:

`RuntimeError: expected policy text not found in methods.html`

Because the workflow used fail-fast sequencing, the following publication step was skipped:

`Commit event, exposure, history and report products`

### Impact

Upstream collection and most enrichment logic still worked. The failed run successfully reached valid event products, but those products were not committed back to the repository. GitHub Pages therefore continued serving older data.

This produced an important distinction:

- the website and GitHub Pages deployment were alive;
- source collection was alive;
- event processing was largely alive;
- the publication chain was blocked by a documentation-maintenance step.

A healthy web endpoint alone was therefore insufficient evidence that Climate Pulse was operationally current.

### Root cause

`scripts/sync_wildfire_identity_docs.py` treated a full, exact block of old prose in `methods.html` as the migration anchor.

The public Methods page had already been updated to the intended wildfire-identity semantics, but later editorial improvements changed the wording and heading. The script did not recognize this semantically current state. It only knew two states:

1. exact old text exists -> replace it;
2. exact old text does not exist -> raise an exception.

It lacked a third valid state:

3. the intended policy is already present in an equivalent current form -> succeed without modification.

The result was a false failure caused by prose drift rather than a runtime-policy defect.

### Unsafe pattern

**Never use a large exact prose block as the sole machine-readable state detector for a recurring operational workflow.**

Human-facing prose is expected to evolve. Whitespace, headings, wording, accessibility edits, or scientific-copy improvements must not make an otherwise valid recurring pipeline non-idempotent.

A second unsafe pattern was allowing a fragile documentation migration to block publication of already generated event products without a robust current-state check.

### Corrective rule 1 — recurring sync scripts must be idempotent

Any script that can run repeatedly in GitHub Actions must explicitly support all normal states:

- **old state**: perform the migration;
- **current state**: exit successfully without rewriting;
- **unknown/conflicting state**: fail closed with a clear diagnostic.

Running the script twice against an already-correct repository must succeed both times.

### Corrective rule 2 — detect semantics with stable anchors, not whole prose paragraphs

Prefer stable machine-readable or structural anchors such as:

- IDs or data attributes;
- short invariant markers;
- configuration/state files;
- dedicated generated sections bounded by stable markers;
- a small set of required semantic phrases when a structured anchor is unavailable.

Do not depend on exact equality of an entire public-facing paragraph or HTML card unless that text itself is an immutable generated artifact.

When semantic markers are used, require enough independent markers to avoid silently accepting an unrelated page.

### Corrective rule 3 — distinguish `already current` from `unexpected drift`

Fail-closed behavior remains desirable, but it must be applied to genuinely ambiguous states.

A safe migration helper should behave conceptually as:

```text
if intended current state is confidently detected:
    succeed with "already current"
elif recognized old state is detected:
    migrate and succeed
else:
    fail with an actionable drift error
```

Do not weaken a safety check into unconditional success merely to keep a workflow green.

### Corrective rule 4 — publication-critical paths require failure-domain review

Before adding a step ahead of the final data commit/publication step, ask:

- Is this step required for the scientific or operational validity of the data being published?
- Can an editorial or maintenance-only failure prevent otherwise valid event products from being committed?
- Is that blocking behavior intentional and tested?
- Can the validation be moved earlier, made idempotent, or separated from the critical publication path?

Documentation consistency may be a valid release gate when it protects scientific meaning, but such a gate must be robust enough for unattended scheduled execution.

### Corrective rule 5 — verify the full publication chain, not only collection

A monitoring run is not considered operationally successful merely because source fetches and enrichment steps succeed.

For scheduled data publication, verify at minimum:

1. source collection completed;
2. canonical/display products were generated;
3. required QC/enrichment completed;
4. documentation/policy gates completed;
5. repository commit/push completed when there are changes;
6. the public data artifact reflects the new generation timestamp/event set;
7. GitHub Pages remains reachable.

When investigating a site that appears empty or stale, check both the public endpoint and the latest workflow run/committed data timestamp.

### Verification required for future changes of this class

For recurring sync/migration scripts, future changes should include or demonstrate the equivalent of these cases:

- **migration case**: recognized old state is converted correctly;
- **idempotency case**: already-current state exits successfully and makes no unnecessary change;
- **drift case**: neither old nor current state is recognized and the script fails clearly;
- **workflow case**: a current repository can reach the downstream commit/publication step.

Where practical, encode these cases as automated tests rather than relying only on manual inspection.

### Fix adopted

The wildfire documentation sync was changed so that it first recognizes the current wildfire-identity semantics using stable markers. If the current state is already present, the script exits successfully. It still fails closed when neither the recognized old state nor the recognized current state is present.

This preserves the original governance intent while removing the false dependency on exact public prose.

---

## 2026-09-13 — Cross-hazard population exposure needs hazard-specific geometry semantics

### Incident

A methods review of the rolling-report population headline raised a basic interpretive question: should Climate Pulse count everyone within a fixed number of kilometres of each reported disaster, or should the spatial rule depend on the hazard?

The implementation already separated wildfire/cyclone exposure-grade footprints from flood context-grade areas and drought risk-grade areas, but the repository did not yet have one canonical, literature-anchored population-exposure policy explaining why those geometries must not be treated as interchangeable.

### Impact

Without a canonical policy, a later maintainer could plausibly introduce an apparently convenient universal buffer, promote a broad reported flood area to primary exposure, or allow a wildfire proximity value to substitute for perimeter exposure. Any of these changes could produce a larger and more visually impressive number while silently changing the scientific meaning of the headline.

### Root cause

Spatial data products that all look like polygons on a map can represent fundamentally different things: burned area, wind hazard, observed inundation, reported administrative/event area, risk area, or a proximity buffer. A generic geometry pipeline does not preserve those distinctions unless semantic grades are explicit and tested.

### Unsafe pattern

**Never infer scientific equivalence from geometric similarity.** A polygon or buffer is not automatically an exposure footprint merely because it can be intersected with a population raster.

A second unsafe pattern is using a single fixed-radius rule across hazards to fill missing spatial evidence. Missing or ambiguous physical extent must not be replaced by a convenient circle simply to preserve a headline number.

### Corrective rule 1 — primary exposure is footprint-based and hazard-specific

The default primary method is:

```text
physically meaningful hazard footprint × gridded resident population
```

Only explicitly exposure-grade geometry may enter the cross-hazard population headline.

### Corrective rule 2 — proximity is a separate semantic grade

Literature- or authority-supported buffers may be scientifically useful, but they must remain separately labelled. For example, a 5 km wildfire buffer can support a near-fire proximity metric, while primary wildfire exposure remains population inside the mapped fire perimeter.

Proximity-grade values must not silently replace footprint exposure in the headline.

### Corrective rule 3 — flood reported areas remain context until inundation is demonstrated

A GDACS flood event/affected-area polygon is context-grade unless the source explicitly represents inundation or an equivalent physically defined flood-hazard footprint. Population inside a reported area may be shown as context, but must not be called primary mapped flood exposure.

A future inundation product may be promoted only after source-specific QC and explicit semantic persistence.

### Corrective rule 4 — exposure is not affected population

Spatial co-location with a hazard footprint supports an exposure estimate. It does not establish injury, displacement, evacuation, mortality, economic loss, or even that every resident was physically present during the event.

Use `affected population` only for source-reported impact counts; do not manufacture it from a population overlay.

### Verification required for future changes of this class

Any change to population-headline eligibility must verify that:

- wildfire perimeter and suitable cyclone hazard geometry remain exposure-grade;
- current GDACS flood event areas remain context-grade;
- current drought risk/impact areas remain risk-grade;
- proximity buffers are not admitted to the cross-hazard headline;
- public Methods and reporting documentation stay synchronized with runtime semantics;
- the canonical `docs/POPULATION_EXPOSURE_STANDARD.md` is updated in the same change set when the scientific policy changes.

### Fix adopted

Climate Pulse adopted `docs/POPULATION_EXPOSURE_STANDARD.md` as the canonical population-exposure policy, updated the public Methods page and reporting documentation, and added regression tests for the current evidence-grade contract.

---

## 2026-09-13 — A cyclone polygon labelled “wind” is not enough evidence for headline exposure

### Incident

A second audit of the rolling population headline found that nearly all of the reported cross-hazard population exposure came from two tropical cyclones. The spatial calculations and geographic deduplication were functioning as designed, but the cyclone footprint selector could classify a GDACS polygon as `gdacs_wind_polygon` when feature metadata merely contained wind-related text.

The resulting period unions covered millions of square kilometres, while the source advisory population metrics were much smaller. This did not prove that the spatial calculation was numerically wrong; it showed that the mapped geometry had not yet demonstrated the physical and temporal semantics required for a primary exposure claim.

### Impact

A generic wind-labelled polygon could enter the cross-hazard headline without proving which wind threshold it represented or whether the polygon was observed/actual, forecast, cumulative, or otherwise modelled. Because cyclone tracks move over time, unioning such polygons across a reporting period can create a very large envelope and a correspondingly large population count.

The risk was therefore semantic inflation: a mathematically valid population overlay could receive a stronger scientific label than its source geometry justified.

### Root cause

The first cyclone fail-closed rule distinguished a generic event-polygon fallback from a wind-labelled polygon, but it still treated the latter label as sufficient evidence. It did not independently require:

- an explicit wind threshold;
- observed/actual rather than forecast or unspecified temporal semantics;
- persistence of those semantics into the daily reporting ledger.

A related naming risk existed in raw GDACS timeline fields `pop39` and `pop74`. Those source-field names must not be converted automatically into scientific claims of 39-kt and 74-kt thresholds when the GDACS methods documentation describes the exposure method using 34-kt and 64-kt wind buffers.

### Unsafe pattern

**Never promote a hazard geometry because a keyword sounds physically specific.** The word `wind` establishes neither threshold nor temporal status.

Likewise, never infer a scientific unit or threshold directly from an opaque upstream field suffix without checking the source methodology.

### Corrective rule 1 — TC headline geometry requires explicit threshold semantics

A tropical-cyclone wind polygon may be exposure-grade only when a supported threshold is explicit and machine-verifiable. Under Population Exposure Standard v1.1, the supported primary thresholds are **34 kt** and **64 kt**, following the documented GDACS TC wind-buffer method.

A method such as `gdacs_wind_polygon` is insufficient. Future eligible methods must preserve the verified threshold explicitly, for example `gdacs_observed_wind_34kt_polygon` or `gdacs_observed_wind_64kt_polygon`.

### Corrective rule 2 — TC headline geometry requires observed/actual temporal semantics

Forecast envelopes and temporally unspecified wind polygons are useful context, but they are not treated as observed primary exposure. A polygon must explicitly establish **observed/actual** semantics before it can enter the cross-hazard mapped-population headline.

If forecast-versus-observed status cannot be proven, fail closed to context-grade.

### Corrective rule 3 — source advisory counts and spatial-union geometry are separate products

Structured GDACS advisory population values can remain visible as source/modelled event-level exposure metrics. They are counts tied to a source advisory and are not automatically spatially unionable across events or days.

The cross-hazard headline, by contrast, requires a common validated geometry layer so overlaps can be removed spatially. Do not use an event-level source count to certify an ambiguous polygon, and do not use an ambiguous polygon to reinterpret a source count.

### Corrective rule 4 — opaque source field names are not scientific definitions

Legacy machine-readable Climate Pulse keys derived from raw GDACS fields may be retained for backward compatibility, but public Methods must follow the verified source methodology. In particular, `pop39` and `pop74` must not be described as literal 39-kt and 74-kt thresholds merely because of their field names.

### Verification required for future changes of this class

Regression tests must prove that:

- generic `gdacs_wind_polygon` is context-grade;
- event-polygon and centre-distance fallbacks are context/screening only;
- forecast wind polygons remain context-grade even when they contain a 34-kt or 64-kt label;
- a threshold without observed/actual status fails closed;
- observed/actual status without an explicit supported threshold fails closed;
- only an explicit observed/actual 34-kt or 64-kt wind polygon can become exposure-grade;
- the public Methods page and canonical exposure standard state the same rule.

### Fix adopted

Population Exposure Standard v1.1 makes tropical-cyclone geometry fail closed on both threshold and temporal semantics. The daily ledger now calls the same canonical policy used by the post-ledger integrity gate, so new records and historical records are evaluated consistently before rolling reports are built.

---

## 2026-09-13 — Hazard type alone does not certify wildfire geometry, and flood source QC does not establish inundation

### Incident

After the tropical-cyclone geometry audit, the remaining cross-hazard population headline was contributed entirely by wildfire geometry. A follow-up audit found that the canonical population policy still classified every mapped wildfire polygon as exposure-grade solely from the event type, even though the workflow already carried stronger GDACS/GWIS episode and burned-area evidence. The same review also found a communication risk in flood QC: selecting a center-aligned GDACS flood polygon could be misread as validating a physical inundation footprint.

### Impact

For wildfire, an episode mismatch, missing source provenance or unrelated polygon resource could theoretically remain headline-eligible because `Wildfire` itself was treated as sufficient evidence. For floods, a successful source-feature selection could be overinterpreted as evidence that the selected polygon represented flooded land rather than a reported event/affected-area context.

### Root cause

The geometry policy encoded scientific semantics mainly at the hazard-type level. It did not require the strongest machine-readable evidence already available for wildfire: GDACS/GWIS provenance, episode identity and consistency between mapped polygon area and source-reported burned area. Flood spatial QC also did not persist an explicit machine-readable statement that inundation had not been established.

### Unsafe pattern

**Never let a hazard category certify a polygon by itself when stronger source-specific evidence exists.** `Wildfire` does not prove that a geometry is the episode-aligned burned-area perimeter.

Likewise, **never let spatial association QC imply physical validation.** A flood polygon being close to the reported event coordinate means it is a plausible source context feature; it does not mean satellite or model evidence has established inundation there.

### Corrective rule 1 — wildfire admission requires an evidence chain

A wildfire footprint may enter the cross-hazard population headline only when:

- the source burned-area metric has explicit GDACS/GWIS provenance;
- the burned-area metric and mapped footprint refer to the same GDACS/GWIS episode;
- a valid source-reported burned-area value is available;
- mapped polygon area passes a coarse consistency guard against the source burned area;
- the result is persisted in `geometry_qc` and the daily ledger.

Missing evidence or a failed integrity check must demote the wildfire geometry to context-grade rather than silently preserving exposure-grade status.

### Corrective rule 2 — area consistency is an integrity guard, not uncertainty quantification

The v1.2 operational gate uses a deliberately broad mapped/source burned-area ratio of **0.5 to 2.0** to detect gross geometry, provenance or episode mismatches. A relative difference above 25% is retained as a warning.

These thresholds are governance/QC controls. They must never be described as a scientific confidence interval, error bound or measurement uncertainty. Future recalibration should be based on audited source distributions, not convenience.

### Corrective rule 3 — flood source selection remains context-only

Flood source-feature QC may suppress unrelated polygons and retain one center-aligned reported event/affected-area feature. The persisted QC must explicitly state:

- `semantic_role = reported_event_area_context`;
- `exposure_grade_eligible = false`;
- `inundation_status = not_established`.

A successful center-alignment check must never upgrade the current GDACS flood polygon to exposure-grade. Very large context areas should carry an explicit warning that their area is not flooded-land area.

### Verification required for future changes of this class

Regression tests must prove that:

- wildfire geometry without admission QC fails closed to context-grade;
- GDACS/GWIS provenance and same-episode alignment are required;
- gross mapped/source burned-area mismatch fails admission;
- moderate area differences can be warned without being mislabeled as uncertainty;
- flood polygons remain non-exposure even when center-alignment QC passes;
- flood QC explicitly persists that inundation is not established;
- public Methods and the canonical exposure standard describe the same rules.

### Fix adopted

Population Exposure Standard v1.2 adds source-specific wildfire admission QC, retrofits eligible historical daily-ledger wildfire records before report generation, and makes flood context semantics explicit in both machine-readable QC and public Methods.

---

## 2026-09-14 — Cross-repository publication must tolerate a moving public branch

### Incident

The private backend successfully built and committed a complete code-free `public-next` artifact, but the final cross-repository push was rejected because `climate-pulse-public-next/main` advanced after the workflow cloned it. GitHub reported that the remote ref was at a newer commit than the publisher expected.

### Impact

The public artifact build, token authentication and local publication commit were all healthy, yet the publication workflow still failed at the last step. The public repository could therefore remain stale even though every upstream stage had succeeded.

### Root cause

The publisher used a clone-once / commit-once / push-once sequence and implicitly assumed the destination branch would remain unchanged between clone and push. An independent Pages/control-file commit advanced `public-next/main` during that window, creating a normal time-of-check/time-of-use race.

### Unsafe pattern

**Never assume a cross-repository generated-artifact branch is immutable between clone and push.** A one-shot push is not robust when another workflow or maintainer can advance the destination branch.

Resolving this class of race with `--force` is also unsafe because the destination intentionally contains remote-only control files, such as its Pages workflow, that must be preserved.

### Corrective rule 1 — serialize the publisher where possible

The backend publisher must use a stable GitHub Actions `concurrency` group with `cancel-in-progress: false` so two runs of the same publication workflow cannot race each other.

### Corrective rule 2 — rebuild on the latest remote head before each bounded push retry

For generated public mirrors, a failed push caused by a moving remote branch should trigger a bounded retry that:

1. fetches the latest `origin/main`;
2. resets the local public checkout to that exact head;
3. reapplies the generated public distribution;
4. preserves intentional remote-only control files;
5. commits the resulting artifact;
6. retries a normal non-force push.

This reconstructs the desired generated state on top of the newest public branch instead of replaying a stale publication commit.

### Corrective rule 3 — preserve remote-only control files explicitly

Publication synchronization must continue excluding `.github/workflows/pages.yml` from destructive mirror deletion. A generated artifact publisher must not erase deployment controls merely because they are absent from `public-dist`.

### Corrective rule 4 — retries must be bounded and diagnosable

Retry indefinitely is not acceptable. The current publisher allows up to five attempts with small backoff intervals and then fails clearly if the public branch continues advancing.

### Verification required for future changes of this class

A publisher change should demonstrate that:

- the code-free artifact still passes its build validation before publication;
- the publisher token can clone and push the target repository;
- an already-synchronized public repository exits successfully without an unnecessary commit;
- a remote branch advance does not require force-pushing and can be absorbed by fetch/reset/reapply/recommit;
- the Pages workflow remains present after publication;
- the workflow either succeeds within the bounded retry budget or fails with an explicit moving-remote diagnostic.

### Fix adopted

`publish-public-next.yml` now serializes publication runs and performs up to five fetch/reset/reapply/commit/push attempts. The first verification run after the change built 901 public files and successfully pushed `public-next/main` from `e9ab06c` to `08ab9bd` on attempt 1 without force-pushing.

---

## 2026-09-15 — Silent snapshot fallback removed enriched Details maps

### Incident

The public site still showed the world map, event feed and expandable Details cards, but the mapped spatial footprint inside expanded Details disappeared even for events whose backend products still contained a valid mapped footprint.

### Impact

The visible site looked broadly healthy, which made the regression easy to miss. Repository-only enrichments were silently lost in the browser, including `footprint.path` and therefore the inline mapped-event footprint. The backend had not stopped generating footprint files; the browser had stopped loading the enriched repository snapshot that referenced them.

### Root cause

The published event schema stores the authoritative display events in `data/events/latest.json` under `canonical_events`. The browser loader `cpLoadRepositorySnapshot()` still accepted only a non-empty `j.events` array.

Consequently the loader returned `null` for a valid current snapshot. `app.js` interpreted that as snapshot unavailability and silently fell back to direct live EONET/GDACS/CEMS parsing. Those live fallback event objects intentionally contain only basic source fields and do not carry backend-only enrichment such as `event.footprint`.

`footprint-ui.js` then behaved correctly but invisibly: its fail-closed guard requires `event.footprint.status === "ready"` and a footprint path, so it returned without inserting the map.

This was therefore a **frontend/public-data schema contract mismatch amplified by a silent degraded-mode fallback**, not a failure of the footprint renderer or the footprint-generation pipeline.

### Unsafe pattern

**Never let a fallback that preserves the basic page shape masquerade as equivalent runtime state when it drops enriched fields.** A site can remain visually populated while losing scientifically important or user-visible features.

A second unsafe pattern is allowing the producer and browser consumer of a versioned public JSON artifact to evolve independently without a publication-time contract check.

### Corrective rule 1 — treat the public snapshot schema as an explicit runtime contract

The browser must support the authoritative current event array (`canonical_events`) and may support legacy `events` only as backward compatibility. The loader should normalize the accepted schema into the shape expected by the rest of the UI rather than making every component understand multiple schema variants.

### Corrective rule 2 — degraded live fallback must not be considered feature-equivalent

Live-source fallback may preserve basic event visibility when the repository snapshot is genuinely unavailable, but maintainers must assume that repository-only enrichment can be absent. Investigations of missing Details features must verify which source mode the browser actually loaded, not only whether events are visible.

Future UI work should make degraded/fallback mode diagnosable rather than silently treating it as equivalent to the enriched snapshot.

### Corrective rule 3 — public builds must validate producer/consumer compatibility

The code-free public build must verify the structural contract between `data/events/latest.json` and the browser loader. For the current schema, a build with non-empty `canonical_events` must prove that the loader recognizes that key and normalizes it to the `events` interface used by `app.js`.

Do not rely only on file-existence checks or the privacy/code-free boundary; those can pass while the website is functionally degraded.

### Corrective rule 4 — ready footprint references must be publication-complete

For every published event whose footprint is marked `status = ready`, the public build must verify that a non-empty path is present and that the referenced footprint JSON is included in the public artifact. A ready metadata reference without its file is a publication-contract failure.

### Verification required for future changes of this class

A future change to the event snapshot schema, browser loader or public distribution must demonstrate that:

- the current `canonical_events` snapshot loads as the repository snapshot rather than triggering live fallback;
- the loader normalizes accepted snapshot data to an `events` array for existing UI code;
- at least the structural ready-footprint references survive that normalization;
- every ready footprint path resolves to a published file;
- browser JavaScript syntax validation includes the snapshot loader;
- an unsupported or malformed snapshot fails the contract check instead of shipping as an apparently healthy but feature-reduced site.

### Fix adopted

`cpLoadRepositorySnapshot()` now prefers non-empty `canonical_events`, retains legacy `events` support, and normalizes the chosen array back to `events` for `app.js`. `scripts/build_public_dist.py` now validates the browser/snapshot schema contract and verifies every ready footprint reference before publication. Backend validation also syntax-checks `assets/sources.js`.

---

## Standing operational invariants

The following rules apply beyond the specific incidents above:

- A scheduled workflow must be safe to run repeatedly against an unchanged repository.
- Public prose may evolve without silently changing runtime policy.
- Runtime policy must not be inferred from arbitrary editorial wording when a structured source of truth can be used instead.
- A green website response does not prove fresh data; freshness requires checking the data-generation/publication chain.
- A successful data fetch does not prove successful publication.
- Cross-repository generated-artifact publication must tolerate a moving destination branch without force-pushing or deleting intentional remote-only control files.
- The browser/public JSON schema is a runtime contract; a feature-preserving live-feed fallback must not hide loss of repository-only enrichments.
- Every published `status = ready` footprint must reference a public file that exists in the same distribution.
- Fail closed on genuine scientific or policy ambiguity, not on harmless formatting/prose drift.
- Primary population exposure must remain hazard-specific and footprint-based; arbitrary universal buffers are not a substitute for missing spatial evidence.
- Context-grade, proximity-grade and risk-grade geometries must not silently enter the cross-hazard exposure headline.
- Wildfire geometry requires source-specific admission evidence; event type alone is not enough.
- Wildfire mapped/source area checks are operational integrity guards and must not be presented as uncertainty intervals.
- Flood center-alignment QC validates source association only; it does not establish inundation or exposure-grade semantics.
- For tropical cyclones, a generic wind label is insufficient: headline geometry requires an explicit supported threshold plus observed/actual temporal semantics.
- Source advisory exposure counts must remain conceptually separate from spatially unionable exposure geometry unless their correspondence is explicitly validated.
- Exposure must not be relabelled as affected population without an upstream impact source.
- Prefer automated regression tests for every previously observed production failure mode or semantic governance gap.
- When a production failure or scientific-governance review teaches a reusable lesson, add it here before considering the issue fully closed.

---

## 2026-09-16 — Climate context success did not guarantee public-event coverage

### Incident

The Climate comparison panel repeatedly showed waiting/unavailable states even though the Earth Engine probe, climate generation workflow and GitHub Pages deployment were green. Two distinct failures were observed: first, per-event climate JSON existed but the browser-preferred `canonical_events` record still lacked its ready reference; later, a newly published CEMS event (EMSR931) existed in `canonical_events` but was absent from the climate index entirely.

### Impact

A user could open a valid public event and see either an indefinite “Waiting for climate enrichment” message or “Climate context is not available for this event yet” even though the climate pipeline itself reported success. Re-running the same incomplete processing universe could leave the state unchanged indefinitely.

### Root cause

The backend carried more than one event view. The browser publishes and prefers `canonical_events`, while climate enrichment historically selected `events` whenever that array was non-empty. Therefore a green climate job proved only that the operational array had been processed; it did not prove that every event actually published to users had been processed.

A related publication race allowed per-event climate JSON, the climate index and event snapshot references to advance at different times. Frontend fallback reduced the symptom but could not create a climate file for an event that never entered the enrichment universe.

### Unsafe pattern

**Never equate successful execution of an enrichment job with complete coverage of the public product.** The relevant universe is the set of entities actually published, not whichever internal array happens to be easiest for the enrichment script to read.

Also, **never use an indefinite waiting message as the default representation of an unknown pipeline mismatch.** Waiting is valid only for a known bounded asynchronous state.

### Corrective rule 1 — process the public union, not one internal view

Climate enrichment must operate on `canonical_events ∪ events`, de-duplicated by stable event ID. Canonical records are preferred for shared IDs because they are browser-facing; missing operational fields may be filled from the secondary record.

### Corrective rule 2 — publication requires an explicit final state for every canonical event

Before publication, every canonical event must satisfy exactly one of these states:

- `climate_context.status = ready` and the referenced per-event JSON exists; or
- `climate_context.status = unavailable` with a non-empty explicit reason.

There is no valid third state meaning “the pipeline did not process this event.” A coverage gap must fail publication rather than be converted into a vague waiting message.

### Corrective rule 3 — related artifacts form one publication contract

`data/events/latest.json`, `data/climate/event_timeseries/index.json`, per-event climate JSON files and the climate UI schema must remain mutually consistent. References are synchronized across both event arrays before public build, and ready paths are verified to exist.

Fast-publish and full-monitor workflows must enforce the same contract; a faster publication path may not weaken coverage guarantees.

### Corrective rule 4 — frontend fallback is defense in depth only

When a snapshot reference is stale or missing, the frontend may attempt the deterministic per-event climate JSON path directly. This prevents an already-generated file from being hidden by stale snapshot metadata. It is not a substitute for backend coverage checks and must not mask a truly missing climate product.

### Corrective rule 5 — validate the product-level invariant in CI

CI must test the full canonical event set with real Earth Engine access when configured. The test must verify that every canonical event is ready or explicitly unavailable, every ready path exists, and every ready event ID appears in the climate index.

A green Earth Engine probe, a green enrichment exit code and a successful Pages deployment are individually insufficient evidence of complete public climate coverage.

### Scientific-display lesson — equal units are not equal statistics

A separate review found that comparing one 24-hour precipitation accumulation directly with a 30-year same-calendar-month precipitation climatology, even after both were expressed in `mm/day`, was too sensitive to precipitation intermittency. Climate Pulse therefore uses seven consecutive complete IFS daily windows for the recent column: seven-day mean temperature, mean of seven daily mean-state VPD values, and seven-day accumulated precipitation divided by seven. Matching units must not be mistaken for matching sampling statistics.

### Verification required for future changes of this class

Future climate-context changes must demonstrate that:

- `canonical_events ∪ events` is the enrichment input universe;
- canonical-only new events are processed in the same run;
- every canonical event has an explicit final climate state;
- every ready climate path exists and is indexed;
- event snapshot references are synchronized before publication;
- browser fallback can recover an already-published per-event file but does not hide backend coverage errors;
- a newly introduced event is spot-checked, not only long-lived events that already had climate files.

### Fix adopted

Climate Pulse now runs climate enrichment through a wrapper that constructs the de-duplicated public event union, executes the existing CRU/IFS enrichment against that union, restores both snapshot views with synchronized references and fails closed on any uncovered canonical event. A second synchronization/coverage gate runs before public build, and regression tests cover canonical-only, events-only and missing-reference cases.
