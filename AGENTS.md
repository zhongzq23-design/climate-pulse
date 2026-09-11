# Climate Pulse agent instructions

These repository instructions are binding for GPT/Codex/other project agents.

## Operational lessons

Before changing GitHub Actions workflows, scheduled automation, data-publication logic, documentation-sync or migration scripts, or other code that can block unattended Climate Pulse updates, read and apply:

`lesson.md`

Treat the failure modes and corrective rules in `lesson.md` as repository-level operational constraints. In particular, recurring automation must be idempotent, machine state must not depend solely on large exact public-prose matches, and changes to publication-critical paths must account for downstream commit/publication failure domains.

When a production or scheduled-run failure reveals a reusable operational lesson, add a concise incident entry to `lesson.md` before considering the incident fully closed. Preserve prior lessons unless the architecture has changed enough to make them obsolete, in which case document the reason.

## Public scientific copy

Before creating, revising or publishing any public-facing methods, methodology, definition, explainer, scientific-description, report-interpretation or limitation text, read and apply:

`docs/PUBLIC_SCIENCE_WRITING_RULES.md`

This includes `methods.html`, `reports.html`, generated report explanatory text, explanatory copy in `index.html`, and future pages whose main purpose is to explain a scientific method, metric, dataset, hazard, exposure, impact, uncertainty or limitation.

### Required editorial behavior

- Preserve the current repository/data/runtime source of truth. Never change a scientific fact merely to improve prose.
- Keep observed, source-reported, modelled, estimated, derived and supplemental quantities distinct.
- Keep exposure, affected population, displacement, damage, risk and attribution distinct.
- Use current project sources first. When wording depends on scientific knowledge outside the immediate page, GPT should consult authoritative project/upstream documentation and, when needed, verified peer-reviewed research.
- Routine wording decisions do **not** require a separate human confirmation step. GPT resolves them autonomously using the evidence hierarchy in the writing rules.
- If evidence cannot safely resolve a scientific ambiguity, use neutral/fail-closed wording or omit the unsupported extension. Do not invent a fact and do not leave a public prompt asking a human to decide.
- Before release, perform the SCFL and final-invariant audits in the writing rules.
- Run `python scripts/check_public_science_copy.py` before publishing public scientific pages.

### No public editorial residue

Do not publish internal labels or unresolved editing notes such as `AUTHOR_DECISION`, `EVIDENCE_REQUIRED`, `needs human review`, `author must confirm`, `TODO`, `TBD`, `XXX`, or placeholder citation requests in public scientific pages.

## Scope boundary

The writing rules govern scientific communication. They do not override data-ingestion, event-selection, source-first metric authority, geometry QC, reporting integrity or `docs/REVIEW_POLICY.md`. If copy and verified implementation disagree, fix the stale copy; do not silently modify runtime behavior to match prose.
