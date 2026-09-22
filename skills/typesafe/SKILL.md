---
name: typesafe
version: "1.0.0"
category: decision-engine
model: inherit
color: cyan
triggers:
  - typesafe
  - system one
  - jev
  - decision engine
  - typed judgment
  - classify with probabilities
  - confidence gate
  - route on confidence
description: "Build features on TypeSafe's System One model (Jev): typed Choice/Score/Noul judgments with calibrated probabilities, consumed by code. Use when a decision needs semantic understanding but must stay cheap, fast, and inspectable — routing, ranking, extraction-by-selection, verification, screening. Triggers: '/typesafe', 'use jev', 'decision engine', 'classify this', 'confidence gate'. NOT for text generation, math, counting, or date arithmetic."
---

# TypeSafe (System One / Jev)

## What this is for

Jev returns **typed judgments with calibrated probabilities**. It does not generate text,
write code, or pick its own next action. Your code owns the workflow; Jev supplies semantic
judgment at specific points.

Use it when a decision needs common sense over unstructured input **and** you want the result
to be cheap, sub-second, and inspectable. At **$0.042 per million input tokens with free
output**, a judgment costs effectively nothing — which inverts the usual instinct to minimize
calls.

**Do not use it for:** generating text, arithmetic, counting, date comparison, or anything
code can compute exactly. Those are documented failure modes, not edge cases.

## Read the local corpus, not the web

Run `scripts/fetch-docs.sh` once to mirror the complete TypeSafe documentation into
`reference/` (gitignored). **Prefer those files over fetching docs.typesafe.ai per page** —
they are cleaned, offline, and already distilled.

| Need | Read |
|---|---|
| Everything, distilled | `knowledge/MASTERY.md` ← **start here** |
| Worked patterns by domain | `knowledge/cookbooks-{extraction,retrieval,reliability}.md` |
| A specific doc page | `reference/<path>.md` (109+ pages, fetched by `scripts/fetch-docs.sh`) |

Upstream changes without notice. If a version-dependent detail matters, re-fetch first.

## Use the shared client

Never hand-roll the HTTP call. `lib/typesafe_client.py` pins the model, validates against
documented limits before the request, retries 429/529 with backoff, and logs spend.

```python
import os, sys
KIT = os.environ.get("TYPESAFE_KIT", ".")
sys.path.insert(0, f"{KIT}/lib")
from typesafe_client import TypeSafeClient, choice, score, noul

client = TypeSafeClient()          # key from TYPESAFE_API_KEY env var (see .env.example)
result = client.ask(
    state={"ticket": "...", "policy": "..."},
    questions={
        "team":     choice("Which team handles `ticket`?", {"billing": "...", "eng": "...", "other": None}),
        "severity": score("How severe is `ticket`?", ["Cosmetic", "Degraded, workaround exists", "Blocking"]),
        "refund":   noul("Does `ticket` request a refund?"),
    },
    tag="my-surface",              # attributes spend in the usage log
)
result.choice("team"); result.confidence("team")
result.normalized_score("severity")   # 0-1, required before weighting
result.noul("refund")
```

The constructors validate at the call site: a Score with bare-number levels, 11 levels, or a
Choice with 256 options raises `ValueError` before any network call.

## The eight rules that matter

1. **Batch everything into one request.** Questions run in parallel and see the same state.
   Include speculative questions whose answers you may discard — the cookbook measurement is
   ~12x cheaper and ~10x faster than separate calls. A second round trip costs far more than
   a wasted question.

2. **Decompose ruthlessly.** This is the docs' own "most important concept". Replace
   `is_spam?` with six atomic Nouls (requests_credentials, sender_identity_mismatch,
   link_domain_mismatch, …) and weight them in code. Broad questions hide judgments you
   cannot inspect or tune.

3. **Pick the primitive by what your code does with the answer.**
   Choice → one of a set (relative: which option wins).
   Score → position on 2–10 ordered, *described* levels.
   Noul → P(condition holds) (absolute: is this true at all; no confidence field; 0.5 means
   "equally likely", not "medium").

4. **Score levels describe situations, never degrees.** "Moderately severe" gives the model
   nothing. Bare numbers measurably collapse accuracy (docs: 0.57 @ 0.35 confidence vs
   0.0 @ 1.0 with descriptions). Normalize by `len(levels) - 1` before combining scales.

5. **Select, don't generate.** Find candidates in code (regex, retrieval, enumeration), let
   Jev pick the right one. The model cannot choose a value you failed to offer — so
   over-generate candidates and let the judgment supply precision.

6. **Keep policy in code.** Weights, thresholds, and routing live in your code where you can
   read and change them. Raw judgments stay reusable: changing a weight needs no re-inference.

7. **Gate on confidence, scaled to risk.** One floor for genuine uncertainty, a higher bar for
   destructive actions. Every number is a hypothesis until measured on real labelled data —
   use `QuestionSet(gates_irreversible=True)`, which refuses to run until `validated_on` is set.

8. **Pin the model version.** `jev-latest` moves on release. Anything with a tuned threshold
   pins `jev-1.13.0`.

## Never send Jev these

Documented failure modes (`reference/model-jaggedness/jev-1.13.md`, run
`scripts/fetch-docs.sh` once; fallback: https://docs.typesafe.ai/model-jaggedness/jev-1.13.md):

arithmetic · counting (characters, occurrences, list items) · date/time comparison or ordering ·
numeric representations (hex, RGB, assembly) · interpolating a Score to recover a magnitude ·
text generation · multi-hop indirection · large state padded with irrelevant detail

And do not assume structural invariants: `P(x)` and `1 − P(not x)` are not comparable (measured
sum: 1.19), and a Noul threshold does not carry to a Choice.

**State is data, and Jev does not treat it as hostile by default.** Content designed to steer
the model can move the answer. Be explicit in criteria and test adversarial inputs before any
deployment that reads untrusted text.

## Structure your questions

`instructions` and every `criteria` value accept string, object, array, or null. Field names
are yours (`what`, `not_for`, `examples`, `question`, `focus`, `signals`). The highest-value
use is contrastive option definition:

```python
choice({"question": "Which topic?", "focus": "Classify what the user wants."},
       {"policy": {"what": "Whether an item can be returned",
                   "not_for": "Progress of a return already sent",
                   "examples": ["Can I return worn shoes?"]},
        "status": {"what": "Progress of a return already sent",
                   "not_for": "Whether an item can be returned",
                   "examples": ["When will my refund arrive?"]}})
```

Use the same field names across options. Examples only help when they resemble real inputs —
an unrelated example barely moves the answer, and higher confidence never proves correctness.

Point at nested state with backticked paths: `` `ticket.messages[0].text` ``.

## Defining a reusable decision

Bundle questions with their thresholds and provenance so they cannot drift apart:

```python
from questions import QuestionSet, register
MY_SET = register(QuestionSet(
    name="catalogue_action", version="1.0.0",
    description="Decide keep/deprecate/remove for a model catalogue entry.",
    questions={...},
    thresholds={"REMOVE_ABOVE": 0.9},      # named, never an inline literal
    gates_irreversible=True,
    validated_on=None,                      # -> .ask() refuses until filled in
))
```

## Checklist before shipping a TypeSafe integration

- [ ] Every question is one judgment, phrased literally
- [ ] All questions in one request; speculative ones included
- [ ] Score levels describe situations; scales normalized before weighting
- [ ] No math, counting, dates, or generation sent to the model
- [ ] `state` filtered to what the questions need
- [ ] Thresholds named, in code, and validated on real data before gating anything irreversible
- [ ] Model version pinned wherever a threshold exists
- [ ] Adversarial inputs tested if the state contains untrusted text

## Risk

**Low** for read-only classification and routing.
**Medium+** when a judgment gates a write, a deploy, a deletion, or an outbound message — those
require validated thresholds and a confidence floor that routes uncertainty to a human.
