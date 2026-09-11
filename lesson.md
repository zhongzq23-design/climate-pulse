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

## Standing operational invariants

The following rules apply beyond the specific 2026-09-11 incident:

- A scheduled workflow must be safe to run repeatedly against an unchanged repository.
- Public prose may evolve without silently changing runtime policy.
- Runtime policy must not be inferred from arbitrary editorial wording when a structured source of truth can be used instead.
- A green website response does not prove fresh data; freshness requires checking the data-generation/publication chain.
- A successful data fetch does not prove successful publication.
- Fail closed on genuine scientific or policy ambiguity, not on harmless formatting/prose drift.
- Prefer automated regression tests for every previously observed production failure mode.
- When a production failure teaches a reusable lesson, add it here before considering the incident fully closed.
