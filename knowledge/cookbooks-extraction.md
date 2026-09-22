# TypeSafe cookbook pattern extraction

> Distilled from the TypeSafe documentation and cookbooks at https://docs.typesafe.ai. Short
> code examples are quoted from there; run `scripts/fetch-docs.sh` for the originals.

Distilled from six TypeSafe cookbooks (`autoformat.md`, `date_extraction_cookbook.md`,
`pre_parsed_value_extraction_cookbook.md`, `sde_cascade.md`, `entity_alignment.md`,
`function_calling.md`).
TypeSafe's System One model (`jev-1.12`, called "Jev") answers typed questions over a
`state` blob and returns a calibrated judgment: `Choice` (pick one of N labeled options,
each with a probability), `Score` (a position among ordered descriptive levels, itself a
distribution), `Noul` (P(yes) for a single yes/no criterion). In every cookbook, code owns
retrieval, candidate generation, normalization, arithmetic, routing, and rendering; the
model supplies only the semantic judgment a human would have to make by reading the text.
This document exists to be reused: when building an extraction/classification/routing
pipeline in an unrelated domain, look here first for the shape of the decomposition before
inventing a new one.

---

## 1. Autoformat (structure recovery)

**Problem.** Reconstruct Markdown (headings, lists, code, callouts, quotes) from plain text
whose formatting was stripped — hard-wrapped mid-sentence lines, no bullets, no `#` marks —
without ever changing a word of the source.

**Decomposition.** Code: splits text into lines, tracks blank-line gaps, tags every line
with a short id (`L014`) and every merged block with another (`B003`); detects "terminal"
sentence-ending punctuation (`.!?:;…`) by regex; merges lines into blocks by applying a
join-probability cutoff that itself branches on the code-observable punctuation fact; and
finally renders the classified blocks to Markdown (heading marks, ordered vs. bulleted
lists, fenced code, blockquotes, `[!WARNING]`-style callouts) — a pure function of the
model's judgments, no free text. Model: pass 1 answers one `Noul` per adjacent line pair
("does this line pick up mid-sentence?"); pass 2 answers one `Choice` per merged block
("what kind of content is this: heading / paragraph / list_item / quote / code /
callout?") plus three conditional companion questions (heading level, is-this-an-ordered-
step, callout kind).

**Primitive choice + why.** `Noul` for the stitch decision — it's a single calibrated
probability the merge routine can threshold, and two different thresholds are applied
depending on a fact code already knows (how the previous line ends), which a single
`Choice` couldn't parameterize as cleanly. `Choice` for block type — six mutually
exclusive categories, and code needs a full probability distribution to display
low-confidence blocks for review, not just a top pick. The companion questions are `Choice`
(heading level, callout kind — categorical) and `Noul` (step-order — a single yes/no about
whether sequence matters).

**Question design.** The entire classifier specification is three flat dicts of one-line
criteria (`TYPE_CRITERIA`, `HLEVEL_CRITERIA`, `CALLOUT_CRITERIA`) plus the step question's
true/false criteria — "there is no other logic. To adapt the pipeline to your own
documents, edit these descriptions." All companion questions (heading level, step-order,
callout kind) are asked **up front, in the same request**, before the block's type is
known, because waiting for the type would cost a third round trip: "the state is most of
the tokens and is sent once either way, while an extra round trip adds a full request of
latency." Most companion answers are simply discarded (e.g. a paragraph's step probability
means nothing and is ignored). Lines/blocks are tagged with short ids (`L000|`, `B003|`)
that questions and answers reference directly, rather than restating text.

**Confidence / threshold use.** `JOIN_AFTER_DANGLING = 0.2` after a line with no
sentence-ending punctuation; `JOIN_AFTER_TERMINAL = 0.5` after one that ends with
`.!?:;…`. Both are explicit tunable constants, derived (in the appendix) from where the
two probability bands actually separated on this document, not fit against labeled data.
`STEP_THRESHOLD = 0.5` is a **group-level** decision — the mean of a list's items' step
probabilities decides ordered-vs-bulleted rendering for the whole list, "a decision no
single question asked directly." The appendix also suggests (not enforces) underlining
for review any block whose winning-type confidence is under 0.55.

**Measured results.** One memo, two API requests total: pass 1 = 16 Noul questions, 0.32s;
pass 2 = 62 questions over 17 blocks, 0.51s; combined 10,211 tokens, 0.8s, quoted as both
"$0.0015" (prose) and "$0.0003" (computed print) in the doc — treat as order-of-magnitude,
not a precise reconciled figure. 28 raw lines → 17 blocks (11 line breaks healed). Ablation:
swapping the stitch question's wording from "does this line pick up mid-sentence" to "are
these two lines part of the same paragraph" collapses 17 blocks to 12 — every unmarked list
item scores >0.75 as "same paragraph" (true in the loose topical sense) and the two lists
wrongly merge into their introducing sentences.

**Transferable rule.** When a judgment feeds a threshold-driven code decision, phrase the
question as the narrowest fact the text itself settles, not a proxy notion (topic,
paragraph) that will systematically fire on structurally-different-but-topically-similar
content. Ask every companion question you might need in the same request as the primary
question, even if most answers go unread — an extra question is cheap, an extra round trip
is not.

---

## 2. Date extraction

**Problem.** Given a document and a role phrase ("the deadline to return the form"),
return a concrete `date` with a confidence, correctly resolving both absolute ("August
14, 2027") and relative ("next Thursday," "today") phrasing, and flagging dates the
document never actually states.

**Decomposition.** Code owns all calendar math: filling in an omitted year (current year,
bumped to next if the resolved date is already more than a month in the past), resolving a
named weekday + offset to a concrete date under an explicit convention (bare weekday = next
occurrence on/after today; `next` = the following calendar week; `current` = this week),
catching impossible dates (`February 30`) via `ValueError`, and pinning a fixed `TODAY`
constant so relative dates resolve reproducibly. The model answers seven `Choice`
questions per role in **one** call: `mode` (absolute / relative / none), then
`month`/`day`/`year` (absolute branch) or `day_anchor`/`weekday`/`week_offset` (relative
branch) — code reads only the branch `mode` selects.

**Primitive choice + why.** `Choice` exclusively — every part is a discrete label
(a month name, a day-of-month, one of 151 years, a weekday), never a binary or ordinal
judgment, so `Noul`/`Score` don't fit. Every criteria dict includes explicit escape
options: `none` ("the document does not state this, or it is not this kind of date") on
every part, plus `out_of_range` on `year` for a stated-but-unlisted year — this lets the
model say "not applicable" rather than being forced onto a wrong label.

**Question design.** One template, parameterized by the `role` string, generates the same
seven questions for any date/role pair in any document — "the date the agreement expires,"
"the deadline to return the form," etc., all reuse `date_questions(role)`. The `year`
question offers a 151-option list (1900–2050) plus the two escapes; the doc flags this as
a design choice you can shrink ("pull the year-like numbers out of the text first and
offer the model only those") if a list that long is undesirable.

**Confidence / threshold use.** `REVIEW_BELOW = 0.60`, explicitly labeled "gate: a date
below this confidence is flagged for a human." A date's confidence is the **minimum**
across every part actually used to assemble it (not all seven — only the ones the chosen
branch touched), so one weak part can send an otherwise-fine date to review. A date that
fails to assemble at all (bad mode, impossible calendar math, out-of-range year) is
`needs_review = True` regardless of confidence.

**Measured results.** 6 worked examples, all correct against a hand-labeled expected date.
Confidences: 0.97, 0.91, 0.95, 0.94, 0.92 for the five real dates; 0.46 for the one the
document never states (mode came back `absolute` with no month attached — an *inconsistent*
partial read, not a clean `none`, which is itself the tell). 5/6 auto-accepted, 1/6 routed
to review.

**Transferable rule.** Decompose a compound value into its atomic named parts and ask a
`Choice` per part, with a `none`/`out_of_range` escape on every part where absence or
out-of-domain values are possible. Never let the model do the composition arithmetic —
resolve entirely in code against a fixed, explicit reference point. A composite value's
confidence is the **min** of the parts it actually used (weakest link), not an average or
product.

---

## 3. Pre-parsed value extraction

**Problem.** Pull a specific email / phone number / money amount out of a document by
role (e.g. "which address should the receipt go to"), guaranteeing the returned string is
copied verbatim from the source — never invented, never digit-transposed.

**Decomposition.** Code: a regex (`find()`, "tune it to over-find") locates and dedupes
every plausible candidate span in document order. Model: a `Choice` question (`pick()`)
whose option set **is** the literal candidate list, selects which span fills the requested
role — plus a `NONE` escape meaning none of them fit. Code again: copies the chosen string
unchanged and normalizes it downstream (`.lower()` on email; `phonenumbers.parse` +
`format(E164)` on phone, using a model-supplied country; `Decimal(re.sub(...))` on money).
Small auxiliary calls (`classify()`, a `Choice` over a fixed label set like currency or
country; `is_true()`, a `Noul`) supply the extra attributes normalization needs.

**Primitive choice + why.** `Choice` is the core mechanism, and the key trick is that the
**options are the candidates themselves** — `criteria = {c: None for c in candidates} |
{NONE: "..."}` — so only the escape hatch needs a description; the candidate spans need
none, because they *are* their own label. Because the model can only select among strings
code already extracted, "it cannot invent a value or transpose a digit." `Noul` is used
for a single binary attribute of an already-chosen value (is this amount a credit, not a
charge) — a yes/no fact about one thing, not a multi-way category. `Choice` again for
small closed label sets (currency, country) that aren't binary.

**Question design.** The same `find`/`pick` pair is reused unmodified across three
unrelated value types (emails, phones, money) — it is a generic role-selection primitive,
"one you can point at your own documents." `pick()`'s docstring states the discipline
directly: "the model chooses, code owns the string."

**Confidence / threshold use.** No numeric gate is coded in the walkthrough, but every
pick prints its confidence (receipt 0.98, sender 1.00, mobile 1.00, country 0.90, is-credit
0.01/0.99) — the pattern implies the same review-threshold gating used in the date-
extraction and sde-cascade cookbooks would apply in production, just left undemonstrated
here. One hard, documented limit: a `Choice` allows at most 255 options; beyond that,
narrow in two stages (pick the section, then the span inside it).

**Measured results.** Email: 4 header addresses correctly split into `receipt` (the
`Reply-To` personal Gmail the body asks for, 0.98) vs. `sender` (the `From` address,
1.00) — both are exact regex-found substrings. Phone: 3 undistinguished local numbers,
`mobile` correctly picked at 1.00, `country` classified `US` at 0.90, normalized to
`+14155550177`. Money: 4 amounts, `total` = `$1,315.50` (charge, P(credit)=0.01), `credit`
= `$50.00` (P(credit)=0.99), both parsed to `Decimal`.

**Transferable rule.** Whenever an output must be a verbatim substring of the input (any
"copy this value out of the document" task), don't generate it — over-generate high-recall
candidates with a cheap deterministic method (regex here; a roster, gazetteer, or NER
model for things regex can't find, like names) and have the model **select** which
candidate fills the role, always with a "none of these" escape. Keep locale/format
normalization (thousands-vs-decimal separator, currency, phone formatting) in code or a
tiny auxiliary classify/Noul call — never in free-text generation.

---

## 4. SDE cascade

**Problem.** Structured-data extraction with a big reasoning model is accurate but slow
and expensive; a small model is cheap but produces confident, **schema-valid**
fabrications a JSON-Schema validator cannot detect. Get most of the big model's quality at
a fraction of its cost.

**Decomposition.** Rung 0 (`gpt-5.4-mini`, text-mode, no structured-outputs/tool-calls/
JSON-mode — deliberately, "a schema-following mistake is not the mistake we expect an LLM
to make," and constrained decoding wouldn't fix a genuinely confused extraction anyway)
does the actual extraction from raw page text, per the prompt's JSON Schema. TypeSafe
(`jev-1.12`) verifies the resulting record field-by-field: for each field it builds a
battery of `Noul` questions (7 metrics for a populated field, 1 for an empty one) whose
`instructions` payload is a structured object — `{field_spec: {path, type, description,
required}, extracted_field: value, main_question: "..."}` — pulled straight from the JSON
Schema plus the extracted value, not hand-written per field. Code applies the gate
(`any_flag`: escalate if **any** per-field metric's P(wrong) exceeds a threshold) and only
then pays for rung 1 (`gpt-5.5`, `reasoning_effort="high"`), substituting its answer for
the whole record.

**Primitive choice + why.** `Noul` for every verification check — each is a narrow,
checkable yes/no about one field against the source (is this hallucinated? does it violate
the declared type? is it absent-but-shouldn't-be?), phrased so `true` **always** means
"something is wrong" (escalate). A holistic `__overall__::judge` head ("should this whole
record be escalated?") is computed and displayed for contrast but **deliberately excluded
from the gate** — the doc frames this as the entire point of the cookbook: "this is what a
typesafe verifier buys you over a blunt 'is this whole thing good?' judge." Aggregation
uses `max` across the per-field battery, not mean: "a per-field flag localizes the error
and stays sparse and strong... `max` ensures one confident red flag escalates, instead of
being averaged into silence."

**Question design.** State passed to TypeSafe is one structured object per record:
`{system_message, instruction, source_text, schema, extraction}` — the model judges the
record *in the context of* the schema and the original instruction, not in isolation. Each
`Noul`'s `instructions` is itself a dict, not a plain string, letting one question template
(`MAIN_QUESTIONS`, 7 metric/criteria pairs) generalize across every field of every schema
with zero per-field prompt engineering. Appendix A enumerates the design discipline
directly and is worth quoting near-verbatim: narrow and grounded (not "is this extraction
good?"); bad = `TRUE`, with explicit criteria stated for both `true` and `false`; per-field
then aggregate with `max`; independent and cheap; separating/calibrated (high on real
errors, low on correct ones, so one threshold cleanly splits).

**Confidence / threshold use.** `FIRE_T = 0.7` — escalate if any per-field metric exceeds
it. Framed explicitly as tunable: in the 100-prompt evaluation this same threshold is swept
0→1, and every resulting (average cost, quality) pair is plotted, producing a Pareto
frontier rather than a single prescribed cutoff.

**Measured results.** Prices (checked Sept 15 2026, $/1M tokens in/out): rung 0
`gpt-5.4-mini` $0.75/$4.50; rung 1 `gpt-5.5` $5.00/$30.00 (~7x rung 0); verifier `jev-1.12`
$0.042/$0.00 (output tokens free). On the 100-scrapegraphai-prompt internal benchmark:
`gpt-5.5-reasoning` alone reaches ≈0.81 quality at ≈$0.10/extraction (the strongest single
model, top-right); the cascade's swept-threshold frontier sits "up-and-left of every single
model." Worked single example (NYU registration-date page with no actual registration
info): mini extraction is schema-valid (`jsonschema.Draft202012Validator(...).is_valid()
== True`) yet fabricates `description: "Registration opens for the fall semester"`
(parroting the schema's own example text); verifier scores `description::hallucinated`
0.95 and `description::off_target` 0.85 (both fire past 0.7), `registration_open_date::
absence_wrong` only 0.14 (correctly not firing — leaving it blank was right); escalation
to the reasoning model correctly empties the fabricated field.

**Transferable rule.** JSON-Schema / type validation is necessary but not sufficient — it
is structurally blind to semantic fabrication. A cheap-extract → semantic-verify →
selective-escalate cascade recovers most of a frontier model's quality for a fraction of
its cost, but only if the verifier is per-field (not holistic), phrased bad-is-true, and
gated with `max` rather than an average.

---

## 5. Knowledge-graph entity alignment

**Problem.** Given 450 candidate duplicate pairs (already narrowed from two full
catalogues by an upstream "cheap but rough" pass, not shown here), decide per pair: merge,
leave unlinked, or route to a human curator — where a false merge is much more expensive
to undo than a missed duplicate.

**Decomposition.** Candidate generation happens entirely upstream (out of scope for this
cookbook). Code's only real logic is `route()`: round the returned `Score` value to its
nearest integer level and look it up in a fixed `OUTCOME` dict — "the whole decision rule:
the nearest level names the outcome," with **no threshold fitted anywhere**. The model
does all of the semantic work: one `Score` question judges the pair's overall relationship
across three ordered levels, and three riding `Noul` questions (same_name, same_brewery,
same_style) supply per-field diagnostic detail — read only when a pair lands in the
ambiguous middle level, to tell the curator *what* disagrees. Alcohol content (ABV) is
explicitly **not** asked about at all: "comparing two numbers is arithmetic; compute it in
code if you want it."

**Primitive choice + why.** `Score`, argued against both alternatives directly in the doc:
a `Noul` would require fitting a threshold on its output after the fact (indirect); a
`Choice` would "lose the ordered relationship" among the three outcomes — "different" and
"same" are poles with "related, but possibly not the same" genuinely between them, not an
unordered third category. Crucially, the three levels are written as the three
**downstream code actions** (leave unlinked / curator queue / assert `sameAs`), not as an
abstract similarity scale — "a semantic label... directly to each outcome, including the
middle outcome," writable before a single pair has ever been scored.

**Question design.** Both full entity records go into one `state` dict (`entity_a`,
`entity_b`) so every question is asked about the *pair*, never about either side alone.
`LEVELS` (three full-sentence descriptions) is the entire specification of what the score
means; `OUTCOME` names each level after the action it triggers, not a generic label — "the
merge outcome is called `assert sameAs` because `sameAs` is the standard way to record that
two entities are the same thing, and writing one is how the merge actually happens." The
doc explicitly flags the **middle-level wording** as the highest-leverage part of the whole
design, since its precision is what decides who reaches the curator vs. settles
automatically.

**Confidence / threshold use.** No manually chosen float threshold exists in the code —
`route(s) = OUTCOME[min(int(s + 0.5), len(LEVELS) - 1)]`, nearest-level rounding, with the
0.5/1.5 cut points following purely from how many levels were written and how, never fit
against labeled data ("both follow from how you worded the levels"). Empirically the two
cuts are unevenly crowded: 9 of 450 pairs sit within 0.1 of the 1.5 cut (the one deciding
what actually merges) versus 47 within 0.1 of the 0.5 cut (deciding curator-or-not) — an
observation about decision-boundary difficulty that falls out for free, without tuning
anything.

**Measured results.** 450 pairs, one request per pair (`ThreadPoolExecutor`,
`MAX_WORKERS=6`; "the public endpoint rate-limits above roughly eight"). Outcome split:
`assert sameAs` 40 (8.9%), `curator queue` 50 (11.1%), `leave unlinked` 360 (80.0%). Worked
examples: `c446` score 1.94 conf 0.92 → sameAs (per-field nouls 0.97/0.99/0.81); `c427`
score 0.03 conf 0.95 → unlinked (all nouls <0.1); `c100` score 1.30 conf **0.27** → curator
(same name 0.95 and brewery 0.94, but disagreeing style wording, style noul only 0.35);
`c428` score 1.10 conf 0.77 → curator (a fruit/hop variant of the same base beer — name
noul 0.63 correctly the lowest of the three, localizing exactly where the two sources
disagree).

**Transferable rule.** When a judgment must drive one of several ordered real-world
actions and a genuine, permanent "needs a human" middle ground exists (not mere
uncertainty, but a legitimately different outcome), use a `Score` whose levels are written
as those exact actions, making the routing function a zero-parameter round-to-nearest.
Attach cheap riding `Noul` questions on the sub-components in the *same* request, so that
whenever a case lands in the ambiguous middle, the diagnosis of *why* is already computed —
no extra call needed.

---

## 6. Function calling

**Problem.** Turn a free-text command ("plot rolling correlation between nvda and spy for
the past month") into a call to one of several existing, **unmodified** Python functions
with typed closed-set arguments, returning per-argument and per-call confidence, without
touching the functions themselves.

**Decomposition.** Code inspects each function's **signature** (`closed_sets()`) and
sorts every parameter into one of three shapes purely from its type hint: `choice`
(`Literal[...]`), `set` (`list[Literal[...]]`), or `flag` (`bool`). Free-form types
(`int`, `str`, dates) are left alone entirely and simply keep their function default — "no
question, and the function's default stands" (demonstrated by `top_movers`'s `limit: int`
argument). A separately authored `spec.json` supplies, per argument, a plain-language
question, a per-option description, and (per the doc's guidance) a "stated" companion
question. `Dispatcher` builds **every** question for **every** function **once** — 54
questions for 10 functions/28 fillable arguments in the worked example — sends them all in
one request per command, and reads back only the answers belonging to whichever function
the `__tool__` choice actually selected.

**Primitive choice + why.** `Choice` for `__tool__` (10 named functions) and for every
scalar closed-set argument (each `Literal` value becomes one option). A `set` argument
(e.g. `symbols: list[Literal[...]]`) becomes **one `Noul` per candidate member** ("Does
the user want NVDA in the comparison?") because membership is an independent per-item
in/out decision, not a mutually exclusive pick — a `Choice` can't express "select any
subset." The `stated` companion is its own explicit yes/no question ("does the user say
anything about this argument at all"), used because a `Choice` that must pick one of its
enumerated values has no built-in way to say "unstated" — functionally the closed-set
analogue of the `none` escape option used elsewhere, implemented as a separate gating
question instead.

**Question design.** Option **keys** in `spec.json` are exactly the literal strings the
function accepts, so no code ever has to map a natural-language label back to an argument
value — the same "the option *is* the value" trick as pre-parsed extraction, but over a
fixed vocabulary instead of found spans. Two explicit authoring rules are stated directly
in the text: write each question about the *idea*, not the words a user might use
("tracking" correctly routes to `rolling_correlation` even though neither "tracking" nor
"lately" appears in `spec.json`), and never name a question after its own parameter
("Which resolution?" gives the model nothing in the command to match against — describe
what the argument *does*).

**Confidence / threshold use.** `call.confidence` = the **minimum** judgment across every
question the call actually used, explicitly justified against a product: "a product...
falls as a function takes more arguments, whether or not any one judgement is shaky" — min
isolates the one genuinely weak inference regardless of how many arguments happen to be
present, while a product conflates argument count with uncertainty. `call.weakest()`
surfaces which specific argument was the bottleneck for debugging/UI purposes (e.g.
`benchmark` at p=0.78 was the weak link in an 0.82-confidence call).

**Measured results.** 10 functions over 156,780 one-minute bars; 28 fillable closed-set
arguments; 54 questions issued per command. 14 worked commands, per-call confidence from
0.53 (a genuinely vague command, "when during the day does nvda trade the most") to 1.00
("what tickers do you have"). Both long, multi-argument commands filled correctly in one
shot: "plot rolling correlation between nvda and spy for the past month" →
`rolling_correlation(symbol='NVDA', benchmark='SPY', window='1mo')` (conf 0.91); "compare
nvda amd and msft over the past three months" → all three tickers correctly included, the
other three correctly excluded, from an identical six-ticker option set on both sides,
disambiguated purely by phrasing ("the one being measured, named first" vs. "the yardstick").

**Transferable rule.** To wire natural language onto an existing typed API without
modifying it, derive the closed-set structure straight from the function signatures (types
as ground truth, not a hand-maintained duplicate schema), pair each parameter with a
plain-language question and per-option description — never the parameter's own name — add
an explicit "was this even mentioned" gate so unstated arguments fall through to the
function's real defaults instead of being guessed, and report call-level confidence as the
**minimum**, never the product, of the judgments actually used.

---

## Cross-cutting patterns in this cluster

1. **Select, don't generate.** In every cookbook, code enumerates or discovers the
   candidate/option set (regex-found spans, `Literal` values, schema field specs, entity
   pair halves, calendar labels) and the model only ever picks among them (`Choice`),
   scores them (`Score`), or answers yes/no about them (`Noul`). Free-text generation is
   never the mechanism by which a final value reaches downstream code.

2. **Always add an escape option.** Wherever absence or non-applicability is possible,
   the `Choice` criteria include an explicit `none` / `out_of_range` / `NONE` label (date
   extraction, pre-parsed extraction) or a dedicated gating question (function calling's
   `stated`). Omitting the escape forces a wrong pick and manufactures false positives.

3. **Ask companion/speculative questions up front, in the same request.** Every
   not-yet-relevant question a later branch might need (autoformat's heading-level/step/
   callout-kind trio; function-calling's 54-questions-for-10-functions; entity-alignment's
   riding nouls) is asked immediately and simply discarded if unused. State tokens
   dominate the cost of a call; a second round trip costs more than a wasted question.

4. **Aggregate escalation/error signals with `max`, never mean.** When multiple Noul
   checks feed a single gate (sde_cascade's `any_flag`), one confident red flag must fire
   the gate even if surrounded by several confident greens — averaging hides rare-but-
   serious errors.

5. **A composite value's confidence is the minimum of its parts, not a product or mean.**
   Date extraction, function calling, and entity alignment's per-field diagnostics all
   converge on this: a product artificially penalizes objects with more parts (confusing
   part-count with uncertainty), while min correctly isolates the single weakest judgment.

6. **Pick the primitive by the shape of the downstream code action, not the shape of the
   question.** `Score` when there are 3+ *ordered* outcomes mapping onto ordered code
   actions with a legitimate middle ground (entity alignment — argued explicitly against
   both `Noul`-plus-threshold and unordered `Choice`). `Choice` when selecting exactly one
   of a known/enumerable set (spans, months, functions, closed-set values). `Noul` when
   code needs one calibrated probability to threshold a binary decision (join/no-join,
   escalate/don't, stated/not, credit/charge, per-item set membership).

7. **Never let the model do arithmetic, calendar math, currency parsing, or string
   normalization.** It reads and judges; code computes — date assembly, phone/E.164
   formatting, `Decimal` parsing, weekday-offset math, Markdown rendering, nearest-level
   rounding are all coded, never asked.

8. **Word the question as the narrowest fact the text itself settles, not a semantically
   adjacent proxy.** Two paired ablations make this concrete: autoformat's "mid-sentence"
   vs. "same paragraph" (17 blocks vs. 12, because "same paragraph" is topically true
   between list items even though the line break was intentional), and sde_cascade's
   per-field battery vs. its holistic `__overall__::judge` head (only the per-field signal
   gates escalation; the holistic head is computed purely for contrast).

9. **Pass structured JSON as the question payload, not a hand-written prompt string, when
   the same question template must generalize across many fields/records/pairs.**
   sde_cascade's `instructions={field_spec, extracted_field, main_question}` and entity
   alignment's `state={entity_a, entity_b}` both let one question definition cover every
   field or pair with zero per-instance prompt engineering.

10. **Treat every numeric threshold as a named, tunable constant, and prefer eliminating
    it entirely when a natural discrete structure exists.** `JOIN_AFTER_DANGLING`,
    `REVIEW_BELOW`, `FIRE_T`, `STEP_THRESHOLD` are all top-of-file constants the docs
    frame as starting points (sde_cascade explicitly sweeps `FIRE_T` 0→1 to trace a
    cost/quality Pareto frontier). Entity alignment goes one step further and removes the
    fitted threshold altogether by using `Score` + round-to-nearest instead of `Noul` +
    cutoff — the preferred move whenever the outcomes really are discrete levels rather
    than a continuous risk to be cut somewhere.
