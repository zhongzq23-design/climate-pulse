# Climate Pulse public science writing rules

Version: **V1**  
Status: **binding for public scientific copy**

## 1. Purpose and scope

These rules govern the writing and revision of public-facing scientific text in Climate Pulse, especially:

- `methods.html` and any future methods/methodology pages;
- explanatory or descriptive pages about hazards, metrics, datasets, calculations, uncertainty, attribution, exposure, impacts, reports or limitations;
- public report introductions, interpretation notes and methodological descriptions;
- explanatory copy embedded in the main website when it makes a scientific or methodological claim.

They do **not** change the underlying data, algorithms, thresholds or scientific-review policy. They govern how those facts are explained. The repository's data and method authorities remain primary.

Before a new or materially revised public scientific page is released, GPT (or another project agent acting under these rules) must perform a complete editorial pass using the process below.

## 2. Scientific meaning firewall

Scientific fidelity outranks elegance. Editing must never invent, delete, strengthen or weaken a claim in a way that changes its scientific meaning.

Preserve exactly, unless the authoritative project source has changed:

- facts, values, units, signs, percentages, ratios and thresholds;
- variables, datasets, source versions and spatial/temporal domains;
- comparison baselines and denominators;
- methods, equations, conditions and processing order;
- source attribution and citation scope;
- whether a value is observed, source-reported, modelled, estimated, derived or supplemental;
- whether a statement is descriptive, statistical, mechanistic, causal, hypothetical or interpretive;
- uncertainty, limitations, negative results and scope restrictions.

Do not turn association into causation, prediction into mechanism, exposure into impact, a modelled count into an observed headcount, or a non-significant result into evidence of no effect.

For Climate Pulse specifically, keep distinctions such as these explicit when relevant:

- **exposed population** is not automatically **affected population**;
- a mapped **event area** is not automatically an observed hazard footprint;
- a drought **risk/impact area** is not confirmed crop loss;
- a GDP exposure proxy is not economic loss;
- local climate context is not event attribution;
- a source-first value can still be modelled rather than directly observed.

## 3. Evidence hierarchy and autonomous GPT resolution

Routine public-copy decisions do not require a separate human editorial confirmation step. GPT acts as the final copy editor and resolves wording choices autonomously using the strongest available evidence.

Use this evidence order:

1. **Current repository source of truth** — runtime code, current data schema, exact source fields, current thresholds, equations, tests and machine-readable provenance.
2. **Current project method documentation** — `methods.html`, `REPORTING.md`, dataset metadata and other versioned project documentation, provided it agrees with runtime/data authority.
3. **Authoritative upstream documentation** — for example GDACS/GDO/GWIS, NASA, JRC, FAO, CRU or World Bank documentation used by the project.
4. **Verified peer-reviewed research** — preferably primary or authoritative literature directly relevant to the scientific claim.
5. **Other high-quality sources** only when the first four levels do not resolve the issue.

When external scientific knowledge is needed, GPT should verify it against existing research rather than ask for routine human wording approval. Any new factual claim that depends on external evidence must be supportable by a real source and should be cited or traceable in the relevant project documentation.

If evidence remains genuinely unresolved or conflicting, do **not** guess. Use the narrowest neutral wording supported by the evidence, preserve the uncertainty explicitly, or omit the unsupported claim. This is the fail-safe replacement for an author-confirmation route.

The model may decide among multiple equally accurate phrasings, structures or emphasis choices. It may not decide factual questions by preference.

## 4. Identify the function of the page before editing

Before rewriting, identify:

- the page type: method, definition, explainer, report interpretation, dataset note or other scientific description;
- the target reader: scientifically literate public, specialist researcher, general user or mixed audience;
- the main question the page should answer;
- the minimum background the reader needs;
- the evidence or method that supports the explanation;
- the appropriate conclusion and its valid scope.

Only edit at the scale actually available. A short definition does not need to imitate the structure of a paper, and a single paragraph should not be judged for lacking a full-page introduction or conclusion.

## 5. Structure: problem, method/evidence, resolution

Use scientific storytelling as functional structure, not decoration. A useful default is:

**Opening → Challenge → Action/Evidence → Resolution**

For a public method or explainer page:

- **Opening:** tell the reader what the metric or method is and why it matters here;
- **Challenge:** identify the specific interpretation or measurement problem;
- **Action/Evidence:** explain the data, calculation, source hierarchy or processing rule;
- **Resolution:** state what the resulting metric means, and just as importantly what it does not mean.

This does not require four headings. The functions can occur within one section or paragraph.

Prefer a direct structure when the reader benefits from the answer early. For technical explanations that depend on definitions or equations, provide only the background needed to understand the calculation before presenting the conclusion.

Do not manufacture novelty, conflict, urgency or a dramatic gap merely to make the page feel more engaging.

## 6. Methods and definitions

Methods text must serve both readers who want to understand the result and readers who want to evaluate or reproduce it.

A strong Climate Pulse methods section usually follows this order:

1. purpose of the metric or calculation;
2. source dataset or upstream authority;
3. variables and units;
4. calculation or selection rule;
5. aggregation, filtering or fallback logic;
6. interpretation;
7. limitations that materially constrain interpretation;
8. source/reference links where needed.

Keep operational details that affect reproducibility or interpretation. Compress standard or repeated material only when the method remains unambiguous.

Equations should define symbols close to where they first appear. State units when they are not obvious. Thresholds must match current runtime code or source rules exactly.

If the implementation and an older written description disagree, the current verified implementation/data authority wins; update the prose rather than harmonizing toward stale wording.

## 7. Claims, uncertainty and causal language

Use the strongest wording the evidence supports, but no stronger.

Prefer precise verbs:

- `measures`, `reports`, `estimates`, `models`, `calculates`, `maps`, `indicates`, `is associated with`, `predicts`, `contributes to` or `causes` only when each is scientifically appropriate;
- do not replace a weaker evidentiary relationship with a stronger one for style;
- do not add generic hedges such as `may`, `might`, `potentially` or `could` unless they express real uncertainty;
- do not remove meaningful qualifiers such as `approximately`, `at least`, `within`, `modelled`, `source-reported`, `estimated` or `supplemental`.

A limitation should appear where the reader first needs it to interpret the claim. Do not bury a limitation that changes the meaning of the headline metric at the bottom of the page.

Do not add generic disclaimers such as “correlation is not causation” when a more specific statement is possible. State the actual boundary: for example, “This polygon is a reported affected-area context, not observed inundation extent.”

## 8. Paragraph design

Each paragraph should have one identifiable main function, while allowing multiple pieces of evidence.

A useful default is:

**point → support/explanation → implication or boundary**

However, evidence-first or condition-first paragraphs are appropriate when the conclusion would be misleading without the setup.

When restructuring:

- keep qualifiers attached to the claims they govern;
- keep citations close to the statements they support;
- do not separate a result from the condition that makes it valid;
- split a paragraph only when it contains multiple independent points;
- merge short fragments when they form one logical arc.

## 9. Sentence clarity and emphasis

Make the scientific actor and action easy to identify. The grammatical subject does not always need to be the scientific actor, but the sentence should not hide the relationship.

Use sentence beginnings to establish the topic and sentence endings to carry important new information when that improves comprehension. Do not force every sentence into the same pattern.

Preserve logical scope when moving phrases. Before relocating a qualifier, condition or parenthetical phrase, identify exactly what it modifies.

Keep long sentences when their logic is clear. Split them when multiple causal, conditional or evidentiary relationships become difficult to track.

Pronouns such as `this`, `it`, `they` or `these` must have a clear antecedent. If not, repeat the precise noun.

## 10. Flow and terminology

Good flow comes from logical continuity, not from adding transition words.

Use `however`, `therefore`, `because`, `thus`, `despite` and similar connectors only when the corresponding contrast, inference or causal relationship is real.

Prefer consistent technical terminology over stylistic synonym variation. Repeating `population exposed`, `burned area`, `VPD`, `reported event area` or another defined term is often better than introducing a near-synonym that changes meaning.

Define abbreviations on first use when the audience may not know them and when the abbreviation will recur enough to be useful.

## 11. Public-audience translation

Treat the audience as intelligent but not necessarily specialized.

When a technical term is necessary:

1. give the formal term;
2. explain it in plain language;
3. connect it to the Climate Pulse use case;
4. preserve the original scientific scope.

For example, a public explanation can define VPD in plain language while retaining the exact SVP/AVP formulation and the limitation imposed by monthly-mean temperature.

Do not turn a modelled exposure into a personal-risk claim, an ecosystem result into a human-health claim, or a scientific result into policy advice unless the evidence actually supports that step.

## 12. Active/passive voice and compression

Use active voice when it clarifies a real actor and action. Use passive voice when the acted-on object is the natural topic or the actor is unknown, irrelevant or already clear.

Do not invent `we`, `researchers`, a mechanism or an agent merely to make a sentence active.

Compress in this order:

1. remove branches that do not serve the page's purpose;
2. remove proposition-level repetition;
3. simplify nominalizations and noun chains when the relationship remains unambiguous;
4. remove empty metadiscourse;
5. shorten individual phrases.

Do not optimize for the shortest possible text. Retain the conditions and limitations needed for correct interpretation and reproducibility.

## 13. Scientific copy should be direct, not defensive

The goal is the clearest defensible statement, not the weakest statement that cannot be criticized.

Do not automatically downgrade a well-supported statement because every citation was not rechecked during routine copy editing. Conversely, when a specific evidence, method or logic problem is found, revise exactly the affected dimension: scope, causality, measurement, statistics, extrapolation, terminology or attribution.

Avoid empty endings such as `more research is needed` or `this may provide insights` unless the page genuinely needs them. End with the most useful supported interpretation or boundary.

## 14. GPT editorial decision protocol

For every material wording question, GPT should classify it internally as one of four cases:

### A. Safe language edit
Grammar, spelling, punctuation, clear reference repair or meaning-equivalent tightening. Apply directly.

### B. Context-supported structural edit
Reordering, paragraph restructuring, terminology harmonization or qualifier placement fully supported by current project evidence. Apply directly and preserve all scientific invariants.

### C. Research-resolved scientific edit
The wording depends on domain knowledge not explicit in the immediate paragraph. Check the repository and, when needed, authoritative documentation or peer-reviewed research. Apply the best-supported wording and keep the source traceable.

### D. Unresolved scientific ambiguity
The available evidence cannot safely select among competing scientific meanings. Do **not** ask for routine human copy approval. Instead, preserve the supplied proposition in neutral language, state the uncertainty if it matters to interpretation, or omit the unsupported extension.

No public page should contain internal workflow labels, unresolved editorial questions or prompts asking the reader/author to choose a scientific meaning.

## 15. SCFL editorial pass

Before publication, perform an internal pass in this order:

### Structure
- Does the page have a clear purpose?
- Does the order match the reader's information needs?
- Do opening and conclusion operate at the same scientific scope?
- Are important limitations placed where they govern interpretation?

### Clarity
- Is each paragraph's main point identifiable?
- Are actors, variables, units, thresholds and referents clear?
- Can readers distinguish source data, modelled values, derived estimates and interpretations?

### Flow
- Do adjacent sentences share a real topic or information handoff?
- Are transitions logically justified?
- Are defined terms used consistently?

### Language
- Are verbs evidentially accurate?
- Are active/passive choices functional?
- Is unnecessary repetition removed without losing conditions or provenance?
- Is the text readable on a public website without oversimplifying the science?

If a language-level edit exposes a structural or scientific problem, return to the earlier step and recheck the affected section.

## 16. Final invariant audit

Before a methods or scientific-description page is released, verify that editing has not changed:

- numbers, signs, units, percentages, ratios or thresholds;
- variable names or operational definitions;
- sample/population/area/time domains;
- baselines or comparison groups;
- event identity rules;
- causal direction or evidence strength;
- negative statements or uncertainty;
- source provenance;
- source-reported vs Climate Pulse-derived status;
- limitations that materially govern interpretation;
- citation ownership or the scope of a source.

Also verify that:

- no unsupported reference, DOI or dataset claim was invented;
- no human-review placeholder remains in public copy;
- no internal editing labels or unresolved questions appear on the page;
- current runtime rules and current public descriptions agree;
- the text is concise enough for the web but still scientifically complete.

When the current wording is already clear, accurate and appropriate, leave it unchanged. Do not manufacture edits merely to demonstrate activity.

## 17. Release rule

For every new or materially revised methods, methodology, definition, explainer or scientific-description page:

1. read this file before editing;
2. identify the page's scientific claims and their authorities;
3. perform the SCFL pass;
4. resolve ordinary scientific wording questions autonomously using current project evidence and verified research;
5. use neutral/fail-closed wording when evidence cannot resolve a scientific ambiguity;
6. run the final invariant audit;
7. remove all internal review notes before publication;
8. run the repository public-copy policy checker before deployment.

This copy-editing rule does **not** override `docs/REVIEW_POLICY.md` or any event/data publication gate. It replaces human confirmation only for the editorial decisions covered here.