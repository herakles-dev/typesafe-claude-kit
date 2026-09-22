# TypeSafe cookbooks: retrieval & classification patterns

> Distilled from the TypeSafe documentation and cookbooks at https://docs.typesafe.ai. Short
> code examples are quoted from there; run `scripts/fetch-docs.sh` for the originals.

Distilled from six TypeSafe cookbooks. TypeSafe's System One model (`jev-1.12`) returns
typed judgments plus calibrated probabilities via three primitives:

- **Choice** — pick one of a set of mutually exclusive options. Returns `choice` (the
  winner), `probabilities` (one per option, summing to 1), and `confidence` (how
  concentrated that distribution is — distinct from the winner's raw probability).
- **Score** — position on ordered descriptive levels (not exercised directly in these six
  cookbooks, included here for completeness of the primitive set).
- **Noul** — probability [0,1] that a yes/no condition holds. Unconstrained: independent
  Nouls in the same request can all read high, all read low, or disagree, because nothing
  forces them to sum to anything.

Across all six cookbooks, the same architectural split recurs: **code owns the workflow**
(retrieval, batching, thresholds, control flow, tree traversal, prompt assembly); **the
model supplies only the semantic judgment** requested of it, one typed question at a time.
Source files (read in full):

- `reference/cookbooks/rerank_typesafe.md`
- `reference/cookbooks/semantic_find.md`
- `reference/cookbooks/hierarchical_classification.md`
- `reference/cookbooks/classifying_rag_passages.md`
- `reference/cookbooks/classification_using_confidence.md`
- `reference/cookbooks/skill_suggestion.md`

---

## 1. Re-ranking (`rerank_typesafe.md`)

**Problem.** A fast-search shortlist contains the right document somewhere, but rarely at
rank 1; you need to re-order the shortlist so the correct passage rises to the top.

**Decomposition.**
- Code: builds the corpus (170 pooled CLERC rows, 3,565 passages, content-hashed IDs so
  shared passages dedupe); runs BM25 (`bm25s`) to produce a 30-candidate shortlist per
  query for 40 evaluation queries; fires the scoring calls concurrently
  (`ThreadPoolExecutor(max_workers=12)`); sorts each shortlist by the returned noul,
  descending; aggregates token counts and cost; renders the before/after charts.
- Model: for each `(query, candidate)` pair, one yes/no judgment — "could this candidate be
  the cited precedent" — via a single `Noul` call. The model never sees the shortlist as a
  set, only one pair at a time.

**Primitive choice + why.** `Noul`, one call per candidate, explicitly *not* batched. The
cookbook's own reasoning: a general-purpose model could produce scores too, but you'd have
to invent a numeric scale and hope repeated calls apply it consistently; a `Noul` already
returns a calibrated, comparable probability under a fixed true/false standard, "faster,
cheaper, and more consistently." Because the question is "how well does *this one*
candidate match" (an absolute, pairwise judgment) rather than "which of these many is
best" (a relative, forced-choice judgment), each pair needs its own full state — so this is
the one cookbook in the set that does **not** fold multiple candidates into a single
request. It explicitly flags this as a simplification: "This walkthrough asked one question
per pair for clarity. A real application would ask several questions about the same pair in
one call" (pointing to the Speculative Fan-Out pattern for packing multiple *questions*, not
multiple *candidates*, into one request).

**Question design.**
```python
is_cited_source = Noul(
    instructions=(
        "The query excerpt comes from a US federal court opinion and was written "
        "immediately around a citation to a precedent; the citation itself has been "
        "removed. Could the candidate passage be from that cited precedent — does it "
        "establish the specific legal proposition the query excerpt invokes at its "
        "citation point?"
    ),
    criteria=NoulCriteria(
        true="The candidate passage states or establishes the specific rule, standard, "
             "holding, or fact pattern that the query excerpt attributes to its removed "
             "citation.",
        false="The candidate passage is merely on a similar topic or doctrine; it does not "
              "supply the specific proposition the query excerpt relies on.",
    ),
)
```
State = `{query_excerpt, candidate_passage}`, identical question object reused for all 1,200
calls (40 queries × 30 candidates); only the state changes.

**Confidence / threshold use.** None — the noul is used purely as a continuous sort key,
not thresholded or gated. This is the simplest use of a probability in the set: rank, don't
decide.

**Measured results (verbatim).**
- Fast search (BM25) alone: correct passage in the top 30 for **100%** of 40 queries; at
  rank 1 for only **5%**.
- After TypeSafe re-rank: top-1 **5% → 18%**, top-5 **15% → 35%**, top-10 **38% → 62%**.
- Cost: **1,200 TypeSafe calls used 1,536,002 input and 25,200 output tokens, costing
  $0.0645** (`jev-1.12` pricing: $0.042 / 1M input tokens, $0.00 / 1M output, as of
  2026-08).

**Transferable rule.** When you need a comparable score for many independent
candidate-against-query pairs, don't invent a numeric rubric for a general LLM — ask a
single yes/no `Noul` with true/false criteria and sort by the returned probability. Reserve
this pairwise-Noul shape for when each candidate needs its own full state scored in
isolation; when candidates can share one state and be distinguished only by an ID or label,
prefer the Choice-batching shape used in the next two cookbooks instead.

---

## 2. Line-by-line search (`semantic_find.md`)

**Problem.** Build semantic search over a document (GitHub's Terms of Service, split into
218 clauses): return the lines that answer a plain-language query, *and* detect when the
document doesn't answer it at all.

**Decomposition.**
- Code: downloads and caches the document; splits into 218 lines; prefixes each with an ID
  (`L000`…`L217`) and rejoins into one `DOCUMENT` string; builds the `Choice` criteria dict
  from the IDs; sends one combined request; unpacks `probabilities` into a `relevance` list
  in document order; turns the raw `exists` noul into a three-way verdict with two
  thresholds; renders a bar chart.
- Model: two judgments per query, in **one request**: which line has the answer (relative,
  forced), and whether the document contains an answer at all (absolute, unconstrained).

**Primitive choice + why.** `Choice` with the **218 line IDs themselves as the options** —
this is the "many candidates scored inside one request" pattern: instead of 218 separate
relevance calls, one `Choice` call returns a full probability distribution over all 218
lines at once. Each option's criteria is `None`, because the option *is* an ID pointing
into text that's already present in `state` — the model doesn't need a separate description
per option when the full content is already in front of it. Paired with an independent
`Noul` ("exists") in the *same* request, because Choice probabilities are forced to sum to
1: some line always ranks first, even when none of them actually answer the query. Only an
unconstrained Noul can fall near zero regardless of what the forced ranking says. Explicit
documented ceiling: **"A `Choice` question accepts up to 255 options, so this recipe
searches documents of up to 255 lines in one request. Past that, search in two passes: one
Choice question picks a window of lines, and a second ranks the lines inside it"** — a
manual two-level beam, the same idea `hierarchical_classification` automates.

**Question design.**
```python
def where_question(query: str) -> Choice:
    return Choice(
        instructions=f'Which line of the document contains the answer to: "{query}"?',
        criteria={line_id(i): None for i in range(len(LINES))},
    )

def exists_question(query: str) -> Noul:
    return Noul(
        instructions=f'Does any line of the document address or answer: "{query}"?',
        criteria=NoulCriteria(
            true="At least one line of the document states or directly implies the answer",
            false="No line of the document addresses this",
        ),
    )
```
Both go to `system_one` together — state (the whole 43,980-character document) is sent
once, so the existence check "requires only a small amount of extra output."

**Confidence / threshold use.** `FOUND, ABSENT = 0.7, 0.35` turn the continuous `exists`
noul into three states: `>= 0.7` → "answered in this document"; `< 0.35` → "not in this
document"; between → "partially addressed." Code comment: "present answers typically read
>=0.9, absent <=0.05" — the thresholds sit with margin below/above the typical extremes.
Explicitly flagged as example values: "tune them against your own documents before using
them in production."

**Measured results (worked examples, not an aggregate eval).**
- *"who owns the code I upload?"* → `exists 0.98`, top line relevance `0.95` vs. next-best
  `0.02` — clean hit.
- *"can GitHub kick me off the platform without warning?"* → `exists 0.97`, top line `0.97`.
- *"do I have to take disputes to arbitration?"* (ToS doesn't cover this) → `exists 0.14` →
  **"not in this document,"** even though the closest-matching line still scores `0.86` on
  relevance — the ranking finds the nearest line regardless; only `exists` catches that it
  isn't actually an answer.
- *"can minors use GitHub with parental permission?"* → `exists 0.46` → **"partially
  addressed"** — the age-13 rule ranks first (`0.90`) but doesn't resolve the parental-consent
  question.

**Transferable rule.** When a forced-choice ranking must coexist with "maybe none of these
qualify," don't try to read that signal out of the Choice distribution itself (it can't
express zero-confidence-in-everything, by construction). Pair it with an independent Noul
asking the absolute version of the same question, in the same request. Separately: putting
candidate IDs directly in as Choice options is a cheap way to score up to ~250 items against
one query in a single request instead of one request per item.

---

## 3. Hierarchical classification (`hierarchical_classification.md`)

**Problem.** Classify a document into the correct leaf of a deep taxonomy — patent
classes (CPC), retail categories (Shopify), biomedical subjects (MeSH), or a codebase's
file tree — where the full leaf set is far too large to enumerate as Choice options.

**Decomposition.**
- Code: parses each hierarchy source into a nested `{label: {child_label: {...}}}` tree with
  no model calls; implements both `greedy_search` (follow the single highest-probability
  child, no recovery) and `beam_search` (retain `K` candidate paths, expand all of them in
  parallel each round, prune to the top `K` by score); computes the path-scoring formula and
  the separation ratio; renders SVG traversal diagrams.
- Model: at each tree node, one `Choice` over that node's **direct children only** — never
  the whole path, never the whole leaf set at once.

**Primitive choice + why.** `Choice`, but atomized to one node (one sibling set) per call,
run repeatedly (iteratively for greedy, in parallel across the beam frontier for beam
search) rather than one giant Choice over every possible leaf. CPC and MeSH have leaf counts
far past any single-request option ceiling, so the only way to reach a leaf is to compose a
path out of many small, cheap decisions. The subtlety: the **full `probabilities` dict
returned by each node's Choice is reused directly as the edge-transition distribution** for
scoring — no second read of "confidence" is needed at the node level; the beam's path score
is built by multiplying the probability of the edge actually taken at each step.

```python
def child_question(labels: tuple[str, ...]) -> tuple[Choice, dict[str, str]]:
    keys = {f"c{i}": label for i, label in enumerate(labels)}
    question = Choice(
        instructions="Which direct child category best matches this document?",
        criteria=keys,
    )
    return question, keys
```

**Formula and its subtleties.**
- `path_score = product(edge_probabilities) ** (1 / decisions)` — geometric mean,
  length-normalized so a 3-level path and a 10-level path can be compared fairly (an
  un-normalized product always favors shorter paths).
- `separation_ratio = top_path_score / second_path_score` — computed and reported, but
  **not used for pruning**; "near 1× is ambiguous… a large ratio means clear separation."
- Precision note baked into the code: use `exp(mean(log(probs)))` rather than the naive
  `product(...) ** (1/n)` for very deep trees (>10 levels) to avoid floating-point underflow;
  an `EPSILON = 1e-9` floor is applied to every probability before multiplying so one
  near-zero edge doesn't zero out an otherwise-strong candidate.
- Noted-but-not-adopted alternative metric: `min(top_prob / second_top_prob)` across all
  nodes on a path, which would instead optimize for paths with unambiguous decisions at
  *every* step rather than a good average.

**Beam mechanics.** `BEAM_WIDTH = 3`, `MAX_DEPTH = 12`. Each round: only candidates with
unexpanded children ("expandable") get a fresh Choice call, dispatched concurrently
(`ThreadPoolExecutor(max_workers=BEAM_WIDTH)`) — one call per beam member per depth level,
not one call per path overall; "finished" candidates (already at a leaf) carry forward
unchanged; after expansion, keep the top `BEAM_WIDTH` of (finished + newly expanded) by
score. This is real beam-search pruning, not just parallel greedy.

**Confidence / threshold use.** No hard accept/reject cutoff — every hierarchy always
returns a definite leaf (unlike the two classification cookbooks below). The
`separation_ratio` exists purely as an observability/diagnostic number.

**Measured results (verbatim table, 4 hierarchies, 1 labeled example each).**

| Hierarchy | Expected leaf | Greedy leaf | Beam K=3 leaf | Greedy correct | Beam correct |
|---|---|---|---|---|---|
| CPC patents | A01K31/12 Perches for poultry or birds | E99Z99/00 Subject matter not otherwise provided for | A01K31/12 Perches for poultry or birds | no | yes |
| Shopify products | Cat Window Beds & Perches | Pet Chairs | Cat Window Beds & Perches | no | yes |
| MeSH biomedical subjects | C06.405.469.432.500 Crohn Disease | (same) | (same) | yes | yes |
| CookSafe files | retrievers.py | (same) | (same) | yes | yes |

"Beam search matched 4 of 4 expected leaves; greedy search matched 2 of 4. Keeping three
paths recovered the expected classification for CPC patents, Shopify products." Both
misses were early greedy mistakes near the root that beam search's width-3 parallel
exploration repaired using deeper evidence.

**Transferable rule.** When a classification target has orders of magnitude more leaves
than any single request can hold as options, don't try to shrink the leaf set — decompose
the taxonomy as a tree and ask one small `Choice` per level (siblings only, typically single
digits to a few dozen options). Run several beams in parallel and prune by
**length-normalized** (geometric-mean) path probability, not raw product, so shallow and
deep candidates compare fairly; greedy search is the same code path at beam width 1 and is
systematically worse whenever the root-level decision is ambiguous. The hierarchy
decomposition also buys per-node/edge observability and unit-testability of individual
decisions as a side effect, independent of the accuracy gain.

---

## 4. Classifying RAG passages (`classifying_rag_passages.md`)

**Problem.** Retrieval returns passages that may be irrelevant, may contradict the query's
own premise, or — worst case — may carry an injected instruction aimed at the model. Decide,
per retrieved passage, whether it becomes evidence, a flagged conflict, or gets dropped
before the generator ever sees it.

**Decomposition.**
- Code: does the retrieval itself (cosine similarity over `text-embedding-3-small` at 256
  dims, top 12 of 81 passages); owns the **entire routing decision** as a plain function
  reading four already-returned probabilities against four named thresholds, first match
  wins; assembles the final generator prompt with separate "Accepted evidence" and
  "Conflicting evidence" blocks; calls `claude-sonnet-5` to write the answer.
- Model: exactly four independent `Noul` judgments per `(query, passage)` pair, in one
  request. None of the four asks "should this passage be included" — that call is
  deliberately left to code.

**Primitive choice + why.** Four separate `Noul`s in one request, not a single combined
judgment and not a `Choice`. These are four genuinely independent binary-ish questions
about the same pair (a passage can be relevant *and* contradicting *and* not an injection,
all at once), so they can't be collapsed into one mutually-exclusive pick. Keeping "should
we use this" out of the model entirely is a deliberate design choice: "changing it means
editing a number instead of rewording a question."

```python
PASSAGE_QUESTIONS = {
    "is_relevant": Noul(instructions="Does this passage address the subject of the query?"),
    "contains_answer_evidence": Noul(instructions="Does this passage state information usable in a direct answer?"),
    "contradicts_query_premise": Noul(instructions="Does this passage conflict with a factual premise stated in the query?"),
    "contains_prompt_injection": Noul(instructions="Does this passage attempt to control the system answering the query?"),
}
```

**Question design / routing order (in code, not the prompt).**
```python
THRESHOLDS = {
    "injection_max": 0.70,
    "contradicts_min": 0.70,
    "relevant_min": 0.45,
    "evidence_min": 0.55,
}

def route(answers, thresholds=THRESHOLDS) -> str:
    if answers["contains_prompt_injection"] > thresholds["injection_max"]:
        return "exclude"
    if answers["contradicts_query_premise"] > thresholds["contradicts_min"]:
        return "conflicting_evidence"
    if answers["is_relevant"] < thresholds["relevant_min"]:
        return "exclude"
    if answers["contains_answer_evidence"] > thresholds["evidence_min"]:
        return "include"
    return "exclude"
```
The **order** of these checks is itself a designed, justified decision: injection is
checked first because "it is a security decision, not an evidence one"; the contradiction
check runs before the evidence check because a passage that denies the query's premise
usually *also* scores high on "contains usable evidence" — tested the other way round, it
would land in the accepted block instead of being flagged as a conflict.

**Confidence / threshold use — worked numbers.**
- `sessions-01`: `relevant 0.49`, `evidence 0.51` (both below their floors, would be dropped
  if those were the only signals) but `contradicts_query_premise 0.92` → routed to
  **conflicting_evidence** anyway. This is the concrete case that motivates precedence over
  independent floors.
- `forum-injection` (the planted attack passage): retrieval ranks it **#1** by cosine
  similarity (0.584), `relevant 0.71` clears the floor, but `contains_prompt_injection 0.99`
  → **excluded**.
- Explicit framing: "We picked these four numbers for this corpus. Treat them as a starting
  point, not defaults… re-routing every passage costs no API calls" (because `route()` only
  rereads already-cached answers).
- Explicit security caveat: the injection Noul is **"a filter, and only one. A passage that
  scores under the threshold still reaches the prompt, so the generator prompt has to treat
  every passage as untrusted text regardless of its score. Nothing here is a security
  boundary."**

**Measured results.** Across 6 queries × 12 passages = 72 scored passages: at least
two-thirds of every query's bar is excluded; only the two deliberately false-premise queries
route anything to the conflict block; two queries (a 30-day-expiry question and "how are
refresh tokens rotated?") accept **nothing** as evidence. Qualitative generation outcomes:
the false-premise query's answer explicitly states it lacks accepted evidence, names the
conflict, and quotes the actual (correct, non-expiring) refresh-token behavior rather than
inventing a 30-day figure; the "how long should an access token live" query cites all 4
accepted passages and shows no trace of the injected instruction in the output text, even
though `forum-injection` was retrieved at rank 1 for a *different* query in the same run.

**Transferable rule.** Separate "what do we know about this item" (several independent
Noul probabilities from the model, same fixed questions every time) from "what do we do
about it" (fixed-order threshold logic that lives in code, not in prompt wording) — this
makes policy changes a code-reviewable constant edit with zero re-scoring cost, and makes
the precedence between competing signals an explicit, testable decision rather than
something buried in a single fuzzy instruction. Defend a generation step against
retrieved/injected adversarial content in layers: a probability-gated filter reduces
exposure, but the generator prompt must *unconditionally* instruct the model to treat all
surviving passages as untrusted text — the filter is not, by itself, a security boundary.

---

## 5. Classification using confidence (`classification_using_confidence.md`)

**Problem.** Classify SEC 10-K "Item 1 Business" filings into one of 75 SIC industry
groups. Most filings are easy; some are inherently ambiguous (a just-divested segment, a
development-stage company describing a business it hasn't started). Need a usable label for
every filing without defaulting to a second model call or human review for every hard case.

**Decomposition.**
- Code: builds the two-level taxonomy (444 four-digit SIC codes → 75 two-digit groups → 10
  divisions by fixed numeric range) entirely from a static TSV, no model involved; builds
  each group's Choice-option description text from its umbrella title plus up to 8 member
  industries (`MAX_NAMED = 8`), because 42 of 75 groups have no natural umbrella name in the
  SEC's own list; `classify()` — "the whole recipe" — is four lines comparing the returned
  `confidence` to a constant and either returning the model's group or algorithmically
  mapping it up to its parent division via a pure lookup function, `division()`.
- Model: one `Choice` over all 75 groups per filing.

**Primitive choice + why.** `Choice`, single request, **75 options in one call** — "a
Choice works reliably up to roughly 240 options, and 75 is well inside that" (the same
option-count ceiling class cited in `semantic_find`, there stated as 255). The key
subtlety: the recipe reads the answer's own **`confidence`** field, not the winning option's
raw probability. Rationale given verbatim: "A winner at 0.45 with a runner-up at 0.44, and a
winner at 0.45 with the rest of the weight scattered thinly, are different situations, and
`confidence` is what separates them" — i.e., the shape of the *entire* distribution matters,
not just the top value.

```python
QUESTION = ("Which broad industry does this company operate in? Judge the company's own "
            "operations as this filing describes them.")

def questions() -> dict:
    return {"group": Choice(instructions=QUESTION,
                             criteria={g: describe(g) for g in sorted(GROUPS)})}

def classify(filing: dict) -> dict:
    answer = ask(filing["id"], filing["text"])
    sure = answer["confidence"] >= CONFIDENT   # CONFIDENT = 0.9
    return {
        "level": "group" if sure else "division",
        "label": answer["group"] if sure else division(answer["group"]),
        "confidence": answer["confidence"],
        "group": answer["group"],
    }
```

**Confidence / threshold use — this is the entire mechanic.** `CONFIDENT = 0.9`. When the
model's confidence clears 0.9, report the specific industry group; when it doesn't, report
the broader division the group sits in instead — computed with **zero additional model
calls**, because the division is a deterministic function of the model's own top choice.
"There is no second call." Every filing still gets a usable label; nothing is dropped or
escalated by default.

**Measured results (verbatim numbers, 60 filings).**
- Forced to always name a group: **39/60 right** overall.
  - Of the 30 filings the model was confident about (≥0.9): **27/30 right** (90%).
  - Of the 30 it was not confident about: **12/30 right** (40%).
- Letting it answer coarsely (division) when unsure: **48/60 "useful answers"** — i.e., the
  same 30 unsure filings scored at the division level go from 40% right to **70% right**
  (21/30).
- Qualitative pattern behind the numbers: the three filings scored at confidence 1.00 were a
  pharmaceutical maker, a life insurer, and a utility — all nominal holding companies but
  each with one obviously dominant, plainly-stated business. The three lowest-confidence
  filings (0.22–0.29) were two development-stage companies describing a business they
  intended to enter rather than one they ran, and one company that had sold one of its two
  segments weeks before filing — i.e., low confidence tracked genuine business-description
  ambiguity, not model noise.

**Transferable rule.** When your label space is hierarchical (specific label rolls up into
a broader, always-correct-if-the-specific-one-is-plausible parent), use the classifier's own
confidence — not the winning probability alone — as a free gate between reporting the
precise label and backing off to its ancestor. The backoff is free because the coarse label
is a deterministic function of the same single answer; no second inference call is ever
needed. This generalizes past SIC codes to any is-a hierarchy: default to precise, fall back
to coarse-but-reliable on a diffuse distribution, and reserve human review or a second model
for cases that don't clear even the coarse bar.

---

## 6. Skill suggestion (`skill_suggestion.md`) — extra depth

**Problem.** An agent (Hermes) carries a roster of 182 skills, shown to it as a
truncated 60-character-description index. On many turns it should load exactly one skill
(and follow it); on many others it should load none. From the truncated index alone the
agent both picks wrong lookalikes (e.g. a `.pptx` *editing* skill when the user wants
*authoring*) and over-loads skills on turns that need none, because "a list of names invites
a guess."

**Decomposition.**
- Code: owns the whole two-request pipeline (`rank_wide` → shortlist top 3 →
  `rerank` → accept/reject), the two hard thresholds, the roster index text
  (`render_index()` — verbatim from Hermes' own prompt builder), the shortlist criteria text
  (full description + first 700 characters of each finalist's `SKILL.md` body), and the
  exact system-prompt string injected as the suggestion.
- Model: which skill(s) are even plausible (Choice, first over all 182, then over the top 3
  with richer text), and two layers of "does this actually apply" abstention checks (Noul).

**Two-request shape, in full detail.**

*Request 1 — "skim all 182 skills" (`rank_wide`), one `system_one` call:*
- One `Choice` named `"which"`, options = all 182 skill names, each option's criteria text =
  that skill's **already-truncated 60-character index description** — the same text the
  agent itself sees, so the classifier gets no informational advantage beyond being asked to
  rank rather than guess. Instructions: *"Which of these skills, if any, is the right one to
  load to help with the user's latest request?"*
- Three separate `Noul` questions about the **request itself**, not about any skill, keyed
  `gate::acts_on_user_system`, `gate::would_follow_documented_procedure`,
  `gate::prose_suffices`:
  ```python
  GATE_QUESTIONS = {
      "acts_on_user_system": "Is the assistant being asked to act on the user's files, "
          "accounts, devices, or online services, rather than only to explain or advise?",
      "would_follow_documented_procedure": "Would a careful expert answering this consult "
          "a specific documented procedure or set of commands, rather than answering from "
          "general understanding?",
      "prose_suffices": "Could a knowledgeable generalist fully satisfy this request in "
          "prose, with no tools, no documentation, and no access to the user's files or "
          "accounts?",
  }
  ```
  These probe from three different angles *whether a skill is needed at all*, deliberately
  **not** by subject matter — subject matter alone can't separate "explain what a monad is"
  (no skill needed) from a coding task that does need one, since both are "about software."
  `prose_suffices` is inverted (`1 - noul`) before averaging, since a "yes" there points
  *away* from needing a skill. The mean of the three oriented nouls is the `gate` score.
  State = `{request, recent_context}`.
- Output kept: top 12 of the 182 ranked options (more than any shortlist needs, keeps the
  cache small) plus the `gate` score — one round trip produces both the ranking and the
  need-a-skill-at-all check.
- Short-circuit: **`if wide["gate"] < 0.30: return ()`** — no second request at all for the
  large "nothing applies" class (173 of 488 test requests were exactly this).

*Request 2 — "read those 3 properly" (`rerank`), fires only past the gate:*
- The same-shaped `Choice`, but now over only the top-3 shortlist, with much richer
  per-option text: `f"{description_full} — {body[:700]}"` (vs. 60 characters in request 1).
  Instructions upgraded accordingly: *"Exactly one of these skills is the right one to load
  for the user's latest request. Which one? Read what each actually does, not just its
  name."*
- Plus **one standalone `Noul` per shortlist candidate**, keyed `fits::{name}`: *"Does the
  skill '{name}' do the specific thing the user's request asks for?"* — each answered
  independently, so all three can score low simultaneously, which the Choice by construction
  cannot do.
- Accept rule: `best = max(fits values)`; if `best < FITS_THRESHOLD (0.30)` → reject the
  whole shortlist, return `()`; otherwise return the **Choice's** winner — not necessarily
  the same skill the highest `fits` noul points to. The cookbook flags this divergence
  explicitly in the pitch-deck example: the `fits` nouls scored the *editing* skill higher
  while the Choice picked the *authoring* skill — "They are deciding different things. The
  Choice settles *which* skill, the nouls settle *whether* to say anything at all."

This is the same Choice+Noul companion shape as `semantic_find`, nested at two scales:
coarse Choice-over-everything + need-a-skill-at-all abstention, then fine
Choice-over-finalists + does-this-one-really-fit abstention. It is progressive disclosure:
every skill is read cheaply once (60-char description), and only the 1–3 finalists are read
in full (700-char excerpt), rather than trying to fit 182 full skill bodies into one
request.

**Injection mechanics.** The winner's name becomes exactly one new block appended to the
system prompt, placed **after** the existing roster text and after its prompt-cache
breakpoint, so the roster itself is byte-identical every turn and prefix caching over the
16,089-character roster block still holds:
```
<skill_relevance>
Relevant to the current request: pptx-author. Ignore this if it does not fit what the user
actually asked for.
</skill_relevance>
```
When nothing clears both thresholds, the block still fires with *"No skill in the roster
appears relevant to this request."* rather than being omitted — because omitting it would
leave the roster's own built-in "err on the side of loading" instruction unopposed. The
"ignore this if it does not fit" phrasing is deliberate and soft: "pushing harder wins
compliance on wrong suggestions too, and a wrong one is worse than none."

**Confidence / threshold use.** `GATE_THRESHOLD = 0.30` (mean of 3 oriented gate nouls —
below it, skip request 2 entirely) and `FITS_THRESHOLD = 0.30` (max per-candidate fit noul
after full-text review — below it, reject the shortlist even though the Choice still picked
a nominal winner). Both are the actual constants used in the published 488-request
measurement, not illustrative placeholders.

**Measured results (verbatim, 488 single-turn requests: 315 covered by exactly one of 171
distinct skills, 173 covered by none, against `claude-haiku-4-5-20251001` as the agent under
test).**

| | wrong loads | needless loads |
|---|---|---|
| agent alone, roster only | 16.8% | 9.8% |
| **agent + TypeSafe suggestion** | **7.3%** | **4.0%** |
| agent handed the correct answer (oracle ceiling) | 2.5% | 1.2% |

"baseline → TypeSafe: **2.3x fewer** wrong loads, **2.4x fewer** needless ones." Even the
oracle (agent told the exact right answer, or told explicitly that nothing applies) doesn't
reach 0%, because the agent sometimes ignores or mis-executes a correct suggestion — a
stated floor on any selection method, however good. Regression detail: of the 315 covered
requests, the suggestion **fixed 37** the baseline got wrong and **broke 7** the baseline had
right — net strongly positive, but not free, "the price of putting one in front of the
turn." Baseline failure analysis: of 36 wrong first picks, 10 came from the right skill's
own category — confirming lookalike-within-category confusion (the `.pptx`
editor-vs-author case) as the dominant failure mode the richer stage-2 text targets.

**Transferable rule.** For "pick at most one of many options for an agent's next step"
problems (skills, tools, plugins, retrieval routes) where a truncated index is the only
thing the agent normally sees: don't try to score every option with full detail in one
request, and don't trust the agent's own guess from the truncated index alone. Run a cheap
wide pass — one Choice over the *entire* catalog using only the text already available,
plus a few Noul questions that probe *whether anything is needed at all* from angles other
than subject matter — to cut to a small shortlist and possibly stop early. Then run a
narrow pass that gives only the finalists their full text and asks both "which" (relative,
forced) and "does each genuinely fit" (absolute, independent per-candidate) — accept only
when the absolute check clears its own floor, regardless of what the relative pick says.
Inject the result as one small, explicitly-overridable addition after the existing prompt,
never a rewrite of it, so caching survives and the agent's own judgment remains the
fallback.

---

## Cross-cutting patterns in this cluster

1. **Code owns the workflow; the model owns one typed judgment at a time.** Retrieval
   (BM25, embeddings), tree traversal and pruning, threshold comparisons, routing/branching,
   and prompt assembly are all plain code in every cookbook. The model is never asked to
   decide *what to do next* — only to answer a specific, fixed question about a specific
   piece of state.

2. **Batch many mutually-exclusive candidates into one request via `Choice`, up to a
   documented ceiling (~240–255 options).** `semantic_find` scores 218 lines in one call by
   making line IDs the options; `classification_using_confidence` scores 75 industry groups
   in one call; `skill_suggestion` scores 182 skills in one call. Reserve this for cases
   where every candidate can be described from a **shared state** and distinguished by ID
   or short label alone.

3. **When each candidate needs its own full state scored independently, don't batch —
   fan out one call per candidate instead.** `rerank_typesafe` deliberately runs 1,200
   separate pairwise `Noul` calls rather than one `Choice` over 30 candidates, because the
   question is "how well does *this* candidate match" (absolute, per-pair) rather than
   "which of these is best" (relative, shared state).

4. **Pair a forced-distribution primitive with an unconstrained one to get an abstention
   signal.** `Choice` probabilities must sum to 1, so a Choice alone can never express "none
   of these are any good" — something always wins. Every cookbook that needs a "maybe
   nothing applies" outcome pairs its Choice with one or more independent `Noul`s in the
   *same* request: `semantic_find` (where + exists), `skill_suggestion` (which + 3 gate
   nouls, then which + per-candidate fit nouls), `classifying_rag_passages` (4 independent
   nouls feeding a code-side gate instead of a Choice at all).

5. **Read the primitive's own confidence/probability as a free routing signal instead of a
   second model call.** `classification_using_confidence` reads `Choice.confidence` (not
   the winner's raw probability) to decide group-vs-division; `hierarchical_classification`
   reuses each node's `Choice.probabilities` directly as beam-search edge weights;
   `rerank_typesafe` uses the raw `Noul` as a continuous sort key; both classification
   cookbooks and the RAG cookbook use `Noul`/`Choice` outputs as hard-threshold gates. In
   every case, the probability *is* the decision input — nothing further is inferred from
   the model.

6. **Hierarchical backoff is free when the coarser label is a deterministic function of the
   finer one.** `division(group)` and `hierarchical_classification`'s tree traversal both
   exploit this: stepping up a level of specificity costs zero additional API calls, because
   it's a pure lookup over the answer already returned.

7. **Thresholds live in code as named, commented constants — not in prompt wording — and
   are explicitly framed as tunable starting points, not defaults.** `THRESHOLDS` (RAG),
   `CONFIDENT = 0.9` (SIC), `GATE_THRESHOLD`/`FITS_THRESHOLD = 0.30` (skills),
   `FOUND`/`ABSENT = 0.7`/`0.35` (line search). The recurring payoff called out in three of
   the cookbooks: because the model's raw probabilities are cached, **re-routing already-
   scored data by moving a threshold costs zero new API calls.**

8. **The order/precedence of threshold checks is a designed decision, not an
   afterthought.** `classifying_rag_passages`'s `route()` checks injection before
   contradiction before relevance before evidence, each with a stated reason (security
   before evidence-quality; contradiction before evidence because a premise-denying passage
   usually also scores high on "contains evidence").

9. **Defending a generation step against adversarial/untrusted retrieved content is
   layered, and the probability gate is explicitly not treated as sufficient on its own.**
   `classifying_rag_passages` states outright that its injection filter is "a filter, and
   only one… Nothing here is a security boundary," and backs it with an unconditional
   instruction in the generator prompt to treat all surviving passages as untrusted text —
   the score-based filter reduces exposure; the prompt-level instruction is the actual
   backstop.

10. **Progressive disclosure: cheap pass over everything with minimal text, expensive pass
    over only a small shortlist with full text.** `skill_suggestion`'s 60-char index →
    700-char excerpts for the top 3; `semantic_find`'s documented two-pass fallback beyond
    255 lines; `hierarchical_classification`'s beam keeping only `K` paths alive per level.
    Combined with the same fixed question(s) applied identically across every item so scores
    stay comparable and sortable (explicit in `rerank_typesafe`, `classifying_rag_passages`,
    and `hierarchical_classification`'s reused per-level question), this is the standard
    shape for scoring more candidates than fit comfortably, or affordably, in one detailed
    request.
