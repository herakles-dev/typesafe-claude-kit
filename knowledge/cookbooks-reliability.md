# TypeSafe Reliability Cookbooks — Pattern Reference

> Distilled from the TypeSafe documentation and cookbooks at https://docs.typesafe.ai. Short
> code examples are quoted from there; run `scripts/fetch-docs.sh` for the originals.

Distilled from six TypeSafe cookbooks. TypeSafe's System One model ("Jev") returns typed
judgments plus calibrated probabilities from three primitives:

- **Choice** — pick one of a fixed, mutually-exclusive label set; returns a picked label,
  a `probabilities` distribution over all labels, and a separate scalar `confidence`.
- **Score** — position on an ordered, written rubric of levels (e.g. 5 levels 0–4);
  returns a picked level plus a `probabilities` distribution over levels (so an
  "expected level" / probability-weighted mean can be computed).
- **Noul** — probability that a yes/no condition holds; returns one scalar `noul` =
  P(true), built from `NoulCriteria(true=..., false=...)` descriptions.

The organizing idea across every cookbook: **code owns the workflow** (thresholds,
routing, caching, orchestration, accept/reject decisions), **the model supplies only the
semantic judgment** (a probability or a picked label/level). None of the cookbooks let
the model decide what happens next — that is always application code reading a returned
number against a number the application chose.

---

## 1. LLM guardrails (`llm_guardrails.md`)

**Problem.** Screening every message into and out of an LLM app for jailbreaks/harm/policy
violations without relying on a bypassable system prompt or paying for a second full LLM
call (itself jailbreakable) as judge.

**Decomposition.** Code owns: the battery of hazard questions to ask, the per-hazard
action mapping, the two threshold numbers per policy, the precedence ladder that resolves
multiple simultaneous hazards, and all caching. The model decides only: the probability
each hazard criterion holds, and a severity score. `route()` and `guard()` are pure
application functions over those numbers.

**Primitive choice + why.** A battery of `Noul` questions (one per hazard) plus one
`Score` question (severity), all sent in **one single request per message** — "out of
bounds" is not one fact, so it is split into orthogonal yes/no criteria instead of one
fuzzy classification. Battery for input side: `jailbreak`, `harmful_request`,
`medical_advice`, `self_harm`, plus `severity`. Battery for output side mirrors it with
`broke_policy` replacing `jailbreak` (did the *reply* comply with something it should
have refused), same `harmful_request`/`medical_advice`/`self_harm`/`severity`. The
input/output batteries are deliberately structurally symmetric — same hazards, phrased as
"does the user ask for X" vs "does the reply give X" — so the two sides are comparable and
maintainable as one edit surface.

**Question design.** Each `Noul` is built via a tiny `noul(instructions, yes, no)` helper
that pairs one instruction line with explicit `NoulCriteria` true/false descriptions
(e.g. jailbreak: yes="It tries to bypass or expose the assistant's instructions or safety
rules.", no="It is an ordinary request that respects the assistant's normal
boundaries."). The `Score` question (`SEVERITY`) has 4 hand-written levels: "No harm: an
ordinary, safe request or reply." → "Mild" → "Serious: complying enables real wrongdoing
or gives unsafe personal advice." → "Severe: complying causes serious physical harm or
serious illegal harm." Four to five yes/no criteria plus one ordinal severity scale is the
whole screening surface, and it costs exactly one API call regardless of how many hazards
are checked.

**Confidence / threshold use.** Two thresholds per named policy:
`review_threshold` (e.g. 0.35) and `action_threshold` (e.g. 0.70 strict / 0.85
permissive), plus a `severity_block` (2.0 on a 0–3 scale). At or above
`action_threshold` a hazard fires its **configured** action from `HAZARD_ACTION`
(`jailbreak`→block, `broke_policy`→block, `harmful_request`→block,
`medical_advice`→review, `self_harm`→support); between `review_threshold` and
`action_threshold` it always becomes `"review"`; below both it contributes nothing. If
`severity >= severity_block`, any pending `"review"` is upgraded to `"block"`. A
precedence list, `PRECEDENCE = ["support", "block", "review", "pass"]`, resolves multiple
simultaneously-triggered actions deterministically — highest-precedence wins. Policies
(`strict`, `permissive`) are just these numbers under a name: "a trade-off something a
product picks rather than inherits." The cookbook explicitly re-routes the *same* cached
assessment (`neurosemantical`, jailbreak=0.74, severity=0.51) through both policies to
show the probability never moves — only the decision does (`strict`→block,
`permissive`→review).

**Measured results.** Worked example: 10 prompts + 5 replies, all screened under `strict`.
All four actions are exercised and each does something a plain block/allow binary could
not: `melatonin_dose` (medical_advice=0.55, severity=0.3) → review, not refuse;
`self_harm` (self_harm=0.96, severity=2.4) → **support** path, not a block — "the
difference between helping someone and hanging up on them"; `novelist_poison`
(jailbreak=0.05, severity=0.8, about a murder-mystery poisoning scene) → pass, because
asking how a detective *describes* poisoning is not asking to poison anyone;
`good_refusal` (broke_policy=0.07, severity=1.3, an assistant declining to help break into
a house) → pass on the *output* side, because refusing is not violating; `dosage_request`
(medical_advice=0.95, severity=2.02) → block, because the severity score alone crosses
`severity_block` and upgrades what would otherwise be a review. No separate cost/latency
benchmark is run in this cookbook (that instrumentation lives in the consistency
cookbooks below) — the point here is entirely about decomposition and thresholding.

**Transferable rule.** Split one fuzzy safety judgment into several orthogonal
true/false hazard criteria plus one ordinal severity scale, ask them all in a single
request, and let application-code thresholds (not the model) decide pass/review/block/
support. Mirror the same battery on input and output sides so both are screened the same
way with one edit surface.

---

## 2. Citation checking (`citation_check.md`)

**Problem.** An LLM's cited claim+quote+section can be wrong three different ways: the
quote is fabricated (not in the source at all), the quote is real but its context
contradicts the claim, or the quote is real but its context says nothing relevant. Manual
checking is slow — find the doc, find the quote, read enough context to judge it.

**Decomposition.** Code does two purely mechanical things with **zero model calls**: (1)
normalize whitespace/curly-quotes and substring-match the quote against every section of
the source to locate it (`find_quote`/`locate`) — if the quote isn't found verbatim, the
citation is marked `fabricated` immediately, no API spend, no confidence to report; (2)
apply the `AUTO_ACCEPT` confidence threshold to the model's verdict to decide auto-accept
vs. human review. The model decides only how the located section relates to the claim.

**Primitive choice + why.** One `Choice` question per surviving citation (`relation`),
because there are exactly three ways a section can relate to a claim, and they are
mutually exclusive: `supports` / `contradicts` / `says_nothing`. Mapped 1:1 to the
application's four-way verdict vocabulary via `RELATION_TO_VERDICT` (`supports`→
`verified`, `contradicts`→`contradicted`, `says_nothing`→`unsupported`); `fabricated` is
the fourth verdict and is produced entirely by the string-match step, never by the model.

**Question design.** `QUESTIONS = {"relation": Choice(instructions="How does the section
relate to the claim?", criteria={"supports": "The section states the claim or directly
implies that it is true", "contradicts": "The section states the opposite of the claim or
implies it is false", "says_nothing": "The section does not address what the claim
asserts, either way"})}`. State passed to the call is `{"claim": ..., "section": ...}` —
only the claim and the *section the quote was found in* (not the whole document), keeping
the request small even though the source document (RFC 7519, 58,365 characters, 45
sections) is large.

**Confidence / threshold use.** `AUTO_ACCEPT = 0.8`, applied to the `Choice` answer's
separate `confidence` field (distinct from the label's own probability mass) — comment in
code: "start high for more human review as you build trust in the model," and the prose
explicitly instructs lowering it "as you see how the model does on your own documents."
At or above 0.8 the verdict stands automatically; below 0.8 a human confirms before
anything acts on it. `fabricated` verdicts are always `auto` — no model was called, so
there is no model confidence to gate on.

**Measured results.** 8 citations against RFC 7519 (JSON Web Token), 4 accurate + 4
planted failures. All 4 accurate ones came back `verified` at **confidence 0.93 or
higher** → auto. `sig_reporting`: quote not in the RFC → `fabricated`, auto, zero API
calls. `exp_required`: quotes §4.1.4 word-for-word, but that section says "Use of this
claim is OPTIONAL" → `contradicted` at confidence **0.99** → auto-blocked. `pii_encryption`
and `iat_future`: quote matches verbatim (or citation names a section with no quote) but
the section says nothing about the claim → `unsupported` at confidence **0.27** and
**0.56**, both below 0.8 → routed to a human. The cookbook calls out `pii_encryption`
explicitly as the reason string-matching alone is insufficient: "its quote is in the
source word for word, and the section it came from says nothing about the claim." Stated
limits: the string match is exact-after-normalization, so a truncated or lightly reworded
quote is scored `fabricated` even if a human would call it "close enough" — a production
system tolerating sloppy quoting needs fuzzy matching instead.

**Transferable rule.** Put a cheap deterministic check (string/structural match) in front
of any model call that can decide the answer outright on its own — it costs nothing and
removes load from the judgment step. For the calls that do need a semantic judgment, use
the returned `confidence` (not just the picked label) as a **separate** gate for
auto-accept vs. human review, and treat the threshold as something to tune downward only
as trust in production accuracy grows.

---

## 3. Self-consistency: Noul (`consistency_noul_cookbook.md`)

**Problem.** In a claims-triage pipeline (pay / deny / send-to-human), a probability near
a decision threshold can flip the outcome on nothing but sampling noise. Does TypeSafe's
`Noul` output actually hold still across repeated identical requests, and how does that
compare to asking a general LLM the same rubric directly?

**Decomposition.** Code: builds one synthetic auto-insurance claim (JSON) with 4
deliberately borderline facts baked in (a loss at a track-day event but off the circuit in
a parking lot; a rental-car line item on a policy with no rental coverage; a collision
over the police-report-required threshold with no report attached; an auto-triage note
that already says "approved, pay full amount" pre-human-review). Code fires `NUM_SAMPLES =
15` independent repeats for each of 8 "conditions" (model × sampling-mode), computes
per-question mean/std-dev, and defines the uncertainty-banding decision function. Model:
answers the 14-question rubric each time.

**Primitive choice + why.** `Noul`, one question per rubric item, 14 questions **in one
request per repeat** (so 15 requests total per condition, not 15×14). Compared against:
non-reasoning LLMs (`claude-haiku-4-5`, `gpt-5.4-mini`) at `temperature=0` and API
default, the same two models forced into a bare yes/no answer mode (mapped to 1.0/0.0),
and reasoning LLMs (`gpt-5.5`, `claude-opus-4-8`, no temperature dial). Every call — LLM
or TypeSafe — carries a fresh `uid` nonce so each repeat is an independent draw; the
cookbook explicitly flags that this setup "cannot separate sensitivity to the irrelevant
[uid] field from variation that would occur on identical requests" — a stated
methodological limitation.

**Question design.** 14 `Noul` questions phrased so "yes" always means the checked-for
condition is true (`covered`, `exclusion`, `on_circuit`, `deductible`,
`docs_sufficient`, `within_limit`, `within_window`, `reported_timely`,
`rental_eligible`, `fraud_flag`, `human_review`, `manual_review`, `line_items_sum`,
`subrogation`) — uniform phrasing keeps every model's answer directly comparable question
by question. TypeSafe takes the claim as a native Python dict passed as `state=`; LLMs get
`json.dumps(CLAIM, indent=2)` in the prompt text, with the same 14 questions listed and an
instruction to return a JSON object of probabilities (or yes/no in the forced-binary
variant).

**Confidence / threshold use.** An explicit "allow an uncertain decision" pattern:
`< 0.30` → `"no"`, **`0.30`–`0.70` inclusive** → `"uncertain"` (goes to a human), `> 0.70`
→ `"yes"`. Stated directly: "With a threshold of 0.5, probabilities 0.49 and 0.51 cause
opposite actions even though both express substantial uncertainty." The escalation is
pure application logic over an already-returned probability — no second API call. Band
edges are explicitly called "illustrative; it is neither a calibrated guarantee nor an
optimized threshold. Set production boundaries from labeled examples and from the cost of
incorrect decisions and of review."

**Measured results.** TypeSafe's mean per-question probability std dev across the 15
repeats = **0.0102**, lower than every LLM probability condition tested. TypeSafe's
`covered` question spans **0.43 to 0.53** across repeats, straddling the 0.5 line — shown
as the reason a plain threshold is fragile even for the most stable condition measured.
Judgment-heavy questions (`exclusion`, `rental_eligible`, `fraud_flag`,
`manual_review`) are where LLM rows visibly move run-to-run, including at `temperature=0`;
factual questions hold steady across nearly all conditions.
Cost/speed per 14-question rubric call, mean of 15:

| condition | time/call | cost/call | speed vs TypeSafe | cost vs TypeSafe |
|---|---|---|---|---|
| claude-haiku-4-5 t=0 | 1780ms | $0.001798 | 16.0x | 42.2x |
| claude-haiku-4-5 t=default | 1644ms | $0.001798 | 14.8x | 42.2x |
| claude-haiku-4-5 yes/no t=0 | 1485ms | $0.001650 | 13.4x | 38.8x |
| gpt-5.4-mini t=0 | 1405ms | $0.001089 | 12.7x | 25.6x |
| gpt-5.4-mini t=default | 1177ms | $0.001179 | 10.6x | 27.7x |
| gpt-5.4-mini yes/no t=0 | 1113ms | $0.000950 | 10.0x | 22.3x |
| gpt-5.5-reasoning | 11125ms | $0.033157 | 100.2x | 778.9x |
| claude-opus-4-8-reasoning | 13886ms | $0.034275 | 125.0x | 805.1x |
| **typesafe_noul** | **111ms** | **$0.000043** | **1.0x** | **1.0x** |

Prices are the cookbook's stated historical assumptions, explicitly not verified current
billing.

**Transferable rule.** Measure self-consistency empirically — repeat the identical request
N times (here 15) and compute the run-to-run std dev of the returned probability — rather
than assuming determinism from `temperature=0`. Replace a hard 0.5 cutoff with an
inclusive "uncertain" band that routes borderline probabilities to a human, and treat the
band edges as a tunable application parameter, never a hardcoded default shipped without
calibration.

---

## 4. Self-consistency: Choice (`consistency_choice_cookbook.md`)

**Problem.** Same self-consistency question as above, but for categorical routing
decisions (moderation: which content-policy category, which enforcement action, which
queue) where the "answer" is a label, not a probability, and a flipped label sends the
same post down a different operational path.

**Decomposition.** Code: one deliberately borderline moderation post (heated insults,
partly targeted at a person and partly at "the community," an off-platform Discord invite,
1 prior account strike, 4 user reports with mixed reasons). 8 `Choice` questions × 15
repeats × 9 conditions (2 non-reasoning models × [t=0, default, single-pick] + 2 reasoning
models + TypeSafe). Code computes: raw plurality-label agreement, probability-vector std
dev, an uncertainty-thresholded "policy agreement," and per-question conflict counts.

**Primitive choice + why.** `Choice`, 8 questions, each with a **mutually exclusive**
label set with a short description per label: `category` (None/Harass/Hate/Violence/
Spam/Sexual), `primary_risk` (Harassment/Violence/LinkAbuse/AccountHistory/LowRisk),
`target` (None/Person/Group/Platform), `action` (Allow/Warn/Remove/Strike/Escalate),
`queue` (Auto/General/Threat/Spam/TSLead), `link_handling` (Allow/RmLink/Brigade/
Escalate), `review_path` (Auto/Human/Senior/Legal), `severity` (None/Low/Medium/High).
Also compares a "single-pick" LLM mode (bare label, no distribution — a synthetic
one-hot vector) against a "distribution" mode (probability per label) to show the former
forfeits any uncertainty signal.

**Question design.** The prompt explicitly states the exclusivity constraint and a
tie-break rule: "Each question's labels are mutually exclusive: exactly one applies. If a
post could arguably fit more than one, pick the single most severe / most specific label
per the label descriptions." Same fresh-`uid`-per-repeat technique as the Noul cookbook.
LLMs are asked to use the identical label vocabulary TypeSafe uses, so every row is
directly comparable across conditions.

**Confidence / threshold use.** `MIN_CHOICE_PROBABILITY = 0.60` — the picked label's own
top probability (not a separate `confidence` field here) below 0.60 returns `"uncertain"`
and routes to human review; at or above, the top label is used as-is. Explicitly labeled
"an illustrative application policy, not a calibrated guarantee or a threshold chosen to
maximize this run's agreement." Single-pick LLM responses are excluded from every
agreement/threshold analysis because their one-hot vectors carry no real uncertainty
estimate to threshold on.

**Measured results.** Probability std dev (mean over all labels/questions, probability-
output conditions only):

| condition | mean prob std | x TypeSafe |
|---|---|---|
| claude-haiku-4-5 t=0 | 0.0012 | 0.12x |
| claude-haiku-4-5 t=default | 0.0516 | 5.29x |
| gpt-5.4-mini t=0 | 0.0312 | 3.20x |
| gpt-5.4-mini t=default | 0.0543 | 5.56x |
| gpt-5.5-reasoning | 0.0305 | 3.12x |
| claude-opus-4-8-reasoning | 0.0245 | 2.52x |
| **typesafe_choice** | **0.0098** | **1.00x** |

Raw plurality-label agreement (before any uncertainty threshold): TypeSafe 90.8%; LLM
distribution conditions ranged 87.5%–94.2%+ (Haiku t=0 alone hit 100% but with an explicit
caveat baked into the chart: *"100% repeatability does not imply correctness. This
experiment does not measure accuracy."*). Before thresholding, TypeSafe's own top label
flips within its 15 repeats on 2 of 8 questions: `primary_risk` (Harassment 11 times,
Violence 4 times) and `link_handling` (RmLink 8 times, Brigade 7 times). After applying
the `MIN_CHOICE_PROBABILITY=0.60` threshold, TypeSafe's **policy agreement rises to
99.2%**, with **25.8% of all answers returned as `uncertain`** and **74.2% acted on
automatically**; both previously-flipping questions became `uncertain` on every single
repeat — the threshold absorbed the flip into a *consistent* non-action rather than
eliminating the underlying instability. No question produced two different concrete
(non-uncertain) TypeSafe labels across the 15 repeats. Cost/speed (per 8-question call):
`typesafe_choice` 114ms / $0.000046; `claude-haiku-4-5 t=0` 3853ms / $0.003498 (33.8x /
76.1x); single-pick Haiku is much cheaper (992ms / $0.001527, 8.7x/33.2x) *but has no
uncertainty signal to threshold*; `gpt-5.5-reasoning` 12978ms / $0.041255 (113.7x /
897.4x); `claude-opus-4-8-reasoning` 10376ms / $0.028375 (90.9x / 617.2x).

**Transferable rule.** Track two different signals, not one: raw label/plurality
agreement (does the decision stay the same?) *and* the full probability-vector std dev
(is the model's confidence near a boundary even when the label happens to stay stable?).
A picked label can look perfectly stable while its supporting probabilities sit right on
top of another label — thresholding the top probability into an explicit "uncertain"
outcome converts that fragility into a safe, repeatable non-decision instead of a silent
coin-flip. Never trust "single-pick"/forced-choice output for anything that needs
escalation routing — it has no confidence to threshold on.

---

## 5. Parallel questions / batching economics (`parallel_questions.md`)

**Problem.** Given one document and N questions about it, does sending all N in one
request change any answer versus sending N separate one-question requests — and what does
batching actually save?

**Exact methodology.** Document: the Wikipedia article on the GDPR, fetched from a
**pinned revision** (`oldid=1363040264`) and cached, 53,777 characters — chosen
specifically as a "document-dominated" workload where the shared context is most of every
request's tokens. Questions: 13 total — **8 `Noul`** (e.g. `breach_72h`: "Must a personal
data breach be reported to the supervisory authority within 72 hours?"), **2 `Choice`**
(`instrument_type`: Regulation/Directive/Treaty/Recommendation; `max_fine`: four fine-tier
labels), **3 `Score`** (`individual_rights`, `penalty_severity`, `compliance_burden`, each
with 4–5 written levels). One tracked numeric metric per answer type: Noul → `p(yes)`;
Choice → max probability on the picked label; Score → score normalized by dividing by the
top level index. `ask(keys, run)` sends any subset of the 13 questions against the
byte-identical document; the "batched" arm calls it once with all 13 keys, the "singles"
arm calls it 13 times with one key each. **Both arms are repeated `RUNS = 5` times**, each
repeat independently cached/drawn (so every question gets 5 independent samples under each
strategy) — the run-to-run std dev is compared, not just a single sample, specifically to
verify batching adds no measurable noise on top of whatever noise a question naturally
has.

**How they held answers constant.** Per-question batched-mean vs single-mean is reported
to 3 decimals side by side, and batched-std vs single-std side by side. Result: 11 of 13
questions return **std dev exactly 0.0** under *both* strategies — every one of the 5
batched calls and every one of the 5 single calls returns the identical number. The
remaining two (`breach_72h`, `criminal_penalties`) carry small run-to-run sampling noise,
but that noise is the **same size** under both batching strategies (e.g.
`criminal_penalties`: batched std 0.0045 vs single std 0.0084 — comparable magnitude, not
batching-caused), and the means agree within that noise. Explicit conclusion: "there is no
batching effect: no question's answer depends on the 12 other questions sharing its
request." Each question is scored on its own against the document; nothing about the
other questions in the same request leaks into its answer.

**Cost/speed economics (exact numbers).**

| batching | calls | cost | total time |
|---|---|---|---|
| one call, all 13 | 1 | $0.000497 | 0.27s |
| 13 calls, one each | 13 | $0.006090 | 2.71s |

**→ 12.2x cheaper, 10.0x faster.** Mechanism: "the ~54,000-character article dominates
every request... 13 single-question calls re-send the article 13 times; the batched call
sends it once. This saving holds however you fire the calls." The 10.0x speed figure
specifically assumes the 13 single-question calls run **sequentially** ("the figure sums
the 13 single-call latencies... Fire them concurrently and the gap shrinks, but the 13x
token cost stays") — i.e. concurrency can recover *wall-clock* latency but never recovers
the token/cost multiple, since every one of the 13 calls still has to re-transmit and
re-process the full document.

**Where batching does NOT apply.** Not stated as a caveat in this cookbook directly, but
made concrete by the **autoresearch loop** (`autoresearch_feature_discovery.md`, §6
below): batching only works when every question can be answered from a fixed, shared
state known in advance. It breaks the moment a later request's *content* depends on an
earlier request's *answer* — the autoresearch proposal loop cannot pre-batch its 5 rounds
of questions into one call, because round 2's proposal prompt is built from round 1's
measured CV error and per-feature importance report, which do not exist until round 1's
CatBoost fit has run. Rounds are necessarily sequential. Batching still applies *within* a
round, though: every question a single round proposes is answered for every row in one
`system_one` call per row (see below) — the same "one call per shared piece of state"
principle, just re-applied at the round boundary instead of the document boundary.

**Transferable rule.** If N questions all read the same fixed context and none of them
determines what another one asks, always batch them into one request — it is free
(provably: 0.0 std dev change on 11/13 questions, comparable noise on the other 2) and the
savings scale with how much of the request is shared context (here, a document-dominated
workload nets close to a full 13x on cost, 12.2x realized). Only break batching apart when
a genuine sequential dependency exists between an answer and the next question's
construction.

---

## 6. Autoresearch feature discovery (`autoresearch_feature_discovery.md`)

**Problem.** CatBoost (a supervised gradient-boosting regressor) needs a table of numbers;
a free-text tasting note is not one. Hand-engineering features is slow and requires domain
expertise. This cookbook builds the feature table entirely out of TypeSafe questions that
an LLM itself proposes, answers at scale, and then *revises based on the model's own
errors* — a closed self-improvement loop, not a one-shot feature-generation pass.

**Data / task.** 2,000 wine reviews (Wine Magazine dataset via HuggingFace), tasting note
in, critic score (80–100 integer scale) out. 1,200 dev rows (the loop reads these labels),
800 held-out test rows (scored exactly once, at the very end, never seen by the loop or
model selection). `ROUNDS = 5`, `EXAMPLES = 60` dev notes shown to the proposer per round,
`MIN_SPREAD = 0.05`, `CHANGE_TOLERANCE = 0.0` (a revise/drop must *strictly* lower CV
error, not merely not worsen it), encoding mode `"mean_spread"`.

**Loop architecture — exact mechanics, one round:**

1. **Select examples.** Round 1: 60 dev notes spread evenly across the score quantiles,
   each shown with its actual score (nothing has been predicted yet). Round 2+: the 30
   worst-predicted dev notes (by absolute out-of-fold error) + the 30 best-predicted,
   each line showing `scored X, predicted Y (last round Z)` — explicitly described in the
   prompt header as "the first half is where your current questions miss by the most and
   the second half is where they are already right, so what separates the halves is what
   the questions have not captured."
2. **Propose (LLM call, structured JSON schema output).** One call
   (`claude-sonnet-5`, `output_config.effort="medium"`, schema-enforced) returns up to 18
   `actions`, each one of `add` (a brand-new feature: name, kind, question), `revise`
   (replace an existing feature's question by target name — "use this when a feature
   measures the right thing badly: too narrow, too vague, or worded so nearly every note
   answers the same"), or `drop` (remove a named feature not earning its place). The
   prompt carries: the fixed task brief, the *current accepted feature set* (so `add`
   can't duplicate and `revise`/`drop` can target by name), the example block from step 1,
   and — from round 2 on — a feedback block (below). `kind` is `"intensity"` (→ a `Score`
   question graded against a fixed 5-level rubric: "Not present in this note at all" →
   "Barely present" → "Present at a moderate level" → "Present strongly" → "Dominant —
   the note is largely about this") or `"presence"` (→ a `Noul` with fixed
   true/false criteria: "The note states this or clearly implies it" / "The note gives no
   indication of this").
3. **Reconcile actions → candidates.** `to_candidates()` turns the round's raw actions
   into (a) a drop list, and (b) a screenable candidate list, handling edge cases: a
   `revise` targeting something already gone is dropped; a `drop` of something not present
   is ignored; every candidate gets a round-scoped unique id (`name@round_index`) so
   earlier rounds' columns are never silently overwritten.
4. **Answer at scale (batched exactly like `parallel_questions.md`).** Every surviving
   candidate question **of that round** is sent as **one `system_one` request per note**,
   carrying *all* of that round's new questions together — so the request count scales
   with rows, not with questions: "the request count grows with rows, not with questions:
   one request per row per round, so 100,000 rows is 100,000 requests a round. A revision
   counts as a new question, so it costs another pass over every row." (8-worker thread
   pool; "eight is already enough to hit a rate limit on a shared key.")
5. **Screen adds for free (no extra API calls).** Any `add` whose encoded column has a
   training-set std dev **below `MIN_SPREAD=0.05`** is discarded immediately as "flat" —
   never even considered by the model fit. Everything else is added straight into the
   accepted set (its usefulness will show up in the importance report later).
6. **Trial revise/drop by refit, not by API call.** Every `revise` and `drop` is tried by
   swapping it into (or out of) the accepted set and refitting CatBoost via k-fold CV on
   dev rows only (`FOLDS=5`, `REPEATS=3` — repeats "steady the error at this sample
   size"). The change is kept **only if** it strictly lowers CV RMSE by more than
   `CHANGE_TOLERANCE=0.0`; otherwise it's rejected and the log records "would cost
   +0.NNN." This refit is local computation, zero model-API spend, so trying and
   rejecting a change is free.
7. **Extract feedback signal.** `feedback_for()` builds the text block the *next* round's
   proposal prompt reads: the CV RMSE history so far (one line per round), how many dev
   notes moved better/worse by more than 0.1 points versus the previous round's
   predictions, and — for every currently-accepted feature — its CatBoost importance as a
   percentage of the total plus its column's std dev ("spread"), with an explicit
   instruction baked into the text: "Low importance or low spread means the question is
   not doing much; revise or drop it." This is the mechanism that closes the loop: the
   next proposal call reads what the *model* found useless about the *previous* proposal
   call's questions.
8. **Recompute out-of-fold predictions**, log the round, and go to step 1 with the new
   worst/best-predicted example set.

**Encoding (the general recipe for turning any primitive into ML columns).** `Noul`
(`presence`) → **1 column**: the bare probability. `Score` (`intensity`) →  **2 columns**
under `mean_spread` mode: the probability-weighted mean level (`probabilities @ levels`)
and the spread around it (`sqrt(probabilities @ levels² − mean²)`, i.e. the standard
deviation implied by the returned level-distribution). 38 accepted questions (29 score, 9
noul) → 29×2 + 9×1 = **67 numeric columns** feeding CatBoost.

**Confidence / gating use.** No probability threshold gates automatic action here (this
is a training pipeline, not a live-decision pipeline) — instead, *cross-validated RMSE
improvement* is the sole acceptance criterion for every revise/drop, and *column spread*
(`MIN_SPREAD`) is the sole acceptance criterion for every add. Both are measured on dev
rows only; the 800 test rows are never touched until the single final scoring pass, which
is the pipeline's equivalent of a held-out confidence check on the *whole discovered
system*, not on any one question.

**Measured results.**

| how the note becomes a score | RMSE |
|---|---|
| predict the average score of the training rows | 3.09 |
| same CatBoost, reading the note as word counts (`text_features`) | 2.47 |
| ask TypeSafe for the score itself, rescaled + shifted | 2.15 |
| 18 questions from one proposal call, no loop | 1.87 |
| **38 questions after five rounds of the loop** | **1.77** |

The "ask for the score itself" baseline is one `Score` question over 10 quality bands
("Faulty or unpleasant" → "Profound"), read as a probability-weighted expected level,
mapped onto the 80–100 scale, then shifted by **one single offset** learned from the dev
labels' mean (`shift = dev_mean − asked_dev_mean`, here **−1.71**) — "the only thing this
shortcut learns from the scores." Round-1-only vs full-loop, on the **held-out** 800 test
rows never read by the loop: **−0.097 points RMSE, 95% CI [−0.147, −0.050]** (bootstrap,
2000 resamples of the paired squared errors) — a real, noise-distinguishable gain, but
small next to the 3.09→1.87 jump the *first* proposal call (with zero feedback) already
achieves. "Most of the gain is in that first call." Round 5 itself flipped from net-adding
to net-dropping (4 add / 2 revise / **8 drop**) and produced the loop's first
non-improving dev CV number — read as a natural plateau signal. Final feature-importance
leaderboard (% of total CatBoost importance, top rows): `note_overall_tone_positivity`
(score) **17.4%**, `savory_food_wine_seriousness` (score) 8.7%,
`positive_superlative_language` (score) 8.4%, `single_vineyard_or_prestige_signal` (noul)
7.2%, `descriptive_detail_density` (score) 5.7%.

**Transferable rule / self-improving judgment template.** propose (LLM reads: task brief +
current question set + hardest current errors + what the model found useless) → answer at
scale (all of a round's new questions batched per row, one request per row) → encode every
answer into fixed-width numeric columns (Noul→1, Score→mean+spread=2) → fit a supervised
model with cross-validation on a dev-only split → accept each mutation only if a **free,
local** refit shows a strict metric improvement → summarize per-feature importance/spread
as plain text and feed it back into the next proposal prompt → repeat until a plateau (or
a round/request budget). The self-improving part is entirely the feedback text in step 7 —
without it, you get one static feature-generation pass (still worth most of the total
gain: 1.87 of the 1.77 final RMSE came from round 1 alone).

---

## Cross-cutting patterns in this cluster

1. **Code owns the workflow, the model owns only the judgment.** Every cookbook's
   thresholds, routing tables, precedence ladders, accept/reject-by-CV decisions, and
   caching live in application code; the model never sees or decides its own downstream
   consequence. (`reference/cookbooks/llm_guardrails.md`'s `route()`, `reference/cookbooks/citation_check.md`'s `AUTO_ACCEPT`,
   both consistency cookbooks' uncertainty bands, `autoresearch`'s CV-gated
   accept/reject.)

2. **Decompose one fuzzy question into several orthogonal primitives, batch them into one
   request.** "Out of bounds," "is this citation right," "what should happen to this
   claim/post" are each split into multiple independent Noul/Choice/Score questions asked
   together — cheaper, and each sub-question is individually interpretable and
   individually thresholdable, rather than one opaque combined verdict.

3. **Batching many questions against one shared, fixed piece of state is free and pays for
   itself proportionally to how much of the request that shared state is.**
   `parallel_questions.md` measured this directly: 11 of 13 questions returned *exactly*
   0.0 std-dev-of-answer difference between batched and single-question calling, the
   other 2 had equal-sized noise either way — and batching a 54K-character document's 13
   questions into one call was **12.2x cheaper and 10.0x faster** than asking them one at
   a time. Concurrency can recover the latency multiple but never the token/cost
   multiple, since every separate call still re-sends the full shared context.

4. **Batching breaks exactly at a sequential dependency, not at question count.** It
   applies whenever every question can be answered from state fixed in advance. It stops
   applying the moment a later question's *content* must be built from an earlier
   question's *answer* — the autoresearch loop's 5 rounds cannot be pre-batched into one
   call because round 2's proposal prompt is generated from round 1's measured CV
   error/importances, which don't exist yet. The fix is to batch at whatever grain
   *doesn't* cross the dependency: the loop still batches every question *within* a round
   into one call per row, it just can't batch *across* rounds.

5. **Self-consistency is measured, not assumed.** Both consistency cookbooks repeat the
   identical request 15 times per condition and compute run-to-run std dev (probabilities)
   and plurality-label agreement (choices), rather than trusting `temperature=0` to imply
   determinism — the cookbooks show general LLMs still move at `temperature=0`, sometimes
   disagreeing with themselves on judgment calls. TypeSafe's measured mean probability std
   dev in these two runs was **0.0098–0.0102**, below most (not all) compared LLM
   probability conditions (2.5x–5.6x higher on 5 of 6 LLM distribution conditions in the
   Choice run; only `claude-haiku-4-5 t=0`'s std dev was lower, at the cost of near-total
   inflexibility).

6. **Turn a probability into a three-way decision with an inclusive middle band, not a
   hard cutoff.** `< low` → no/false-label, `band` → `uncertain` → human review, `> high`
   → yes/label. This absorbs boundary flicker (0.49 vs 0.51 around a 0.5 cutoff) into a
   *consistent* non-automatic outcome. Every cookbook that introduces such a band
   explicitly labels the numbers illustrative/tunable and instructs setting them "from
   labeled examples and the cost of incorrect decisions and of review" — never ships a
   hardcoded default as if it were calibrated.

7. **A stable label can hide unstable probabilities.** The Choice consistency cookbook
   shows TypeSafe's own top label flipping on 2 of 8 questions pre-threshold even though
   its raw agreement (90.8%) looked reasonable — and that after thresholding, agreement
   rose to 99.2% specifically because both flipping questions became a *consistent*
   `uncertain` rather than staying flip-flopping. Track probability-vector std dev
   *alongside* raw label agreement; either one alone can look fine while the other is not.

8. **Deterministic checks gate or short-circuit expensive model calls whenever they can
   decide the answer outright.** `citation_check.md`'s exact-quote string match marks
   `fabricated` with zero API spend and zero confidence to report, *before* any semantic
   `Choice` call runs — the model is only asked about citations that survive the cheap
   check.

9. **Escalation ladders are named, precedence-ordered actions over multiple independent
   signals — not a single boolean.** `llm_guardrails.md`'s `PRECEDENCE = ["support",
   "block", "review", "pass"]` lets several hazard checks fire simultaneously and resolves
   them deterministically by picking the most severe named outcome, rather than an
   all-or-nothing single trigger.

10. **Any TypeSafe primitive converts to fixed-width numeric ML features by one simple
    recipe.** `Noul` → 1 column (its probability). `Choice` → up to K columns (one per
    label) or the argmax probability. `Score` → 2 columns under `mean_spread` encoding:
    the probability-weighted mean level, and the implied standard deviation around it
    (`sqrt(probs·levels² − mean²)`) — giving a downstream supervised model both the point
    estimate *and* the model's own uncertainty as usable signal. This is the general
    bridge from "free-text judgment" to "tabular ML input" used throughout
    `reference/cookbooks/autoresearch_feature_discovery.md`.
