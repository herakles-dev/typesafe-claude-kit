# START HERE — TypeSafe home base

You are an agent with a TypeSafe task. This file is the map. **Read the block below, then jump
to the one route that matches your task.** Do not read the whole repo; you will spend context
you need for the work.

This file lives at the kit root (this repo). If you've installed the kit elsewhere, set
`TYPESAFE_KIT` to that path — the snippets below fall back to it.

---

## The 60-second orientation (everyone reads this)

**TypeSafe's System One model, Jev, answers typed questions about a state you give it, and
returns calibrated probabilities. It does not generate text, write code, or choose its next
action.** Your code owns the workflow; Jev supplies judgment at specific points.

Three primitives, and the choice between them is the first real decision:

| Primitive | Answers | Returns | Nature |
|---|---|---|---|
| **Choice** | Which one of this set? | `choice`, `probabilities`, `confidence` | **Relative** — which option wins |
| **Score** | Where on these ordered levels? | `score`, `legend`, `probabilities`, `confidence` | 2–10 levels, each *describing a situation* |
| **Noul** | Does this condition hold? | `noul` (0–1) — **no confidence field** | **Absolute** — is this true at all |

Cost: **$0.042 per million input tokens, output free.** ~300–600ms per call. This is cheap
enough that the correct instinct is to ask *more* questions, not fewer.

**Never send Jev:** arithmetic · counting · date comparison or ordering · hex/RGB/assembly
reasoning · text generation · Score interpolation to recover a number · multi-hop indirection ·
large state padded with irrelevant detail. These are documented failure modes, not edge cases.

**The fit test — is TypeSafe even right here?** All three must hold:
1. The decision needs *semantic* understanding (code alone can't express it), **and**
2. the answer space is *bounded* — you can enumerate options, levels, or a yes/no, **and**
3. you want the result *inspectable* — a probability your code reads, not prose.

If any fails, say so and stop. A lookup belongs in code. Generating prose belongs to Claude.
Choosing a next action in a loop belongs to an agent. **Recommending against TypeSafe is a
valid, useful outcome** — record why.

---

## First: ask the corpus what you need

Before picking a route by hand, start at `knowledge/MASTERY.md` — it is the distilled index: a
60-second orientation, the four patterns, and section pointers into `reference/`, the fetched
upstream corpus (run `scripts/fetch-docs.sh` once to populate it).

The routes below send you to *sections*, not whole files — use them to find your task quickly,
or to see the shape of the corpus at a glance.

## The toolbench

Tools in `tools/` use TypeSafe on our own artifacts, so a specialist's judgment is available
to you without spawning the specialist. Each is advisory — they gate nothing — and each is
cheap enough to run without thinking about it.

| Tool | Ask it | Costs |
|---|---|---|
| `question_critic.py --file d.json --state s.json` | Is this drafted question any good, and what do I fix | ~$0.0001 |
| `jaggedness_screen.py --file design.json` | Which of the nine failure modes my design risks, plus four added locally: attacker-settable premise, correlated evidence, unbanded cutoff, unmeasured threshold | ~$0.001 |
| `consistency_probe.py --set X --inputs f.json` | Are my answers stable, or do they only look stable | ~$0.00003/call × repeats |
| `question_health.py --set X --inputs f.json --group-by <path>` | Does this question carry any information at all on these inputs | ~$0.0001/input |
| `confidence_accuracy_curve.py --set X --labels f.json --answer <q> --expected <field>` | Where does accuracy actually degrade, and what does each cut cost | ~$0.0001/case |

```bash
python3 tools/jaggedness_screen.py --schema        # the design JSON it expects
python3 tools/question_health.py --set X --inputs i.json --group-by probe.http_status
```

**When to reach for which:**

- Starting anything → `knowledge/MASTERY.md`, then the route below. It replaces guessing what to read.
- Drafted a question → `question_critic`. It reads for atomicity, primitive fit, and level
  quality. It has three documented false positives; read them before obeying a flag.
- Before writing code against a design → `jaggedness_screen`. Cheaper to learn at design time
  that you asked the model to count than to find out in production.
- Before trusting any threshold → `consistency_probe` (is it stable?) and `question_health`
  (does it vary with the input at all?), then `confidence_accuracy_curve` (where does accuracy
  break?). Those three are orthogonal and none substitutes for another.

## Standing rules, each paid for in a real mistake

These are not style. Every one cost work on 2026-09-20; the evidence is in `MASTERY.md` §9.

1. **Build test state the way production builds it.** Route test inputs through the same
   builder the caller uses. A labelled file's field named `state` was not the state — it
   carried three fields the production builder strips — and two agents independently measured
   a payload the system never sends. It produced two reproducible, entirely false defects.

2. **One run is not a measurement.** Jev is self-consistent, so a single run reproduces its own
   error exactly and reads like evidence. Report mean ± sd over repeats, never min/max of one
   pass. Single-run numbers misled this project four separate times in one day.

3. **A tool's flag needs a control before you obey it.** `question_critic` flagged one Score
   as multi-dimensional; three rewrites failed to move it. Controls proved the flag was a false
   positive — a single-dimension Score still scored 0.43. Isolate the variable, then decide.

4. **Validate the gate end to end, not per threshold.** Every threshold can be individually
   defensible while the composed decision fails. Only running the whole procedure over real
   data surfaces that.

5. **A safety rule that lives in prose is one the next caller skips.** If a rule must hold —
   an ordering, a corroboration requirement — put it in code and test it against the real
   payload, not against a description of it.

6. **A count is not coverage.** `question_health` reports how often a question crossed 0.5; it
   cannot tell a positive that fired for the intended reason from one that fired for the wrong
   one. Read the cases before any threshold rests on them.

7. **Prefer surfaces whose ground truth labels itself.** HTTP status, exit codes,
   did-the-container-recover. We can build anything; we can only *calibrate* what has labels,
   and that is the binding constraint on everything here.

8. **Read the probabilities, not the verdict.** A 0.58 and a 0.95 both clear a 0.55 cut and
   mean very different things.

9. **Verify the load-bearing claim, not the checkable one.** A specialist reported that an
   internal escalation queue's outcomes were "already being recorded — no hand-labeling
   needed". That single line decided which surface to build next. It was derived from a
   pipeline description, never from the data, and it was wrong: 137 transcripts, 17 with a
   `final` record, **one** negative example, and the paid-model resolution is adjudicated
   in-session and never written back.

   The lead had verified that specialist's *other* claims — checked a duplicate entry closely
   enough to downgrade it from "confirmed" — and felt diligent while the one claim the whole
   ranking rested on went unmeasured. Then repeated it in the next status update as fact.

   Easy-to-check claims are not the ones to check. Before acting on a report, identify which
   single sentence the decision depends on, and open the data behind *that* one. It was caught
   by the receiving reviewer, who measured instead of building.

Two limits worth stating plainly: **stability is not accuracy** (a question can be perfectly
stable and perfectly wrong — which is why `consistency_probe` refuses to emit `validated_on`),
and **a screen is not a guarantee** (`jaggedness_screen` flags exposure at design time; it does
not replace `typesafe-adversary` probing what you built).

## Choose your route

Find your task. Read only what its route lists.

### Route A — "Should we use TypeSafe for X?" / "What could we build?"
→ `knowledge/MASTERY.md` §1 (what it is), §7 (the four patterns), §11 (where this lands in a platform like yours)
→ `reference/concepts/use-case-map.md` if brainstorming breadth
**Own it:** `typesafe-architect`, or `typesafe-scout` for open-ended discovery.
**Produce:** a shape — what stays in code, what becomes judgments, how many requests. Not code.

### Route B — "Design the questions for X"
→ `knowledge/MASTERY.md` §3 (primitives), §5 (structure), §6 (the design method)
→ `knowledge/cookbooks-extraction.md` if selecting values out of text
→ `knowledge/cookbooks-retrieval.md` if ranking, routing, or classifying into a taxonomy
**Own it:** `typesafe-question-smith`.
**Produce:** the `questions` dict, with criteria written contrastively.

### Route C — "Implement / wire it up"
→ `lib/typesafe_client.py` docstring (the whole API surface)
→ `knowledge/MASTERY.md` §2 (API facts), §6 step 7 (composition in code)
→ `tests/test_client.py` as the worked example
**Own it:** `typesafe-engineer`.
**Produce:** a registered `QuestionSet` + composing code + a live test.

### Route D — "Pick / justify a threshold" or "is this reliable?"
→ `knowledge/MASTERY.md` §4 (confidence), §9 (observed divergence — read this one)
→ `knowledge/cookbooks-reliability.md` §self-consistency and §escalation ladders
**Own it:** `typesafe-calibrator`.
**Produce:** thresholds measured on labelled data, with the data named in `validated_on`.

### Route E — "Break it" / "is this safe against hostile input?"
→ `reference/model-jaggedness/jev-1.13.md` (all 9 failure modes, in full)
→ `knowledge/MASTERY.md` §8, §9
→ `knowledge/cookbooks-reliability.md` §guardrails
**Own it:** `typesafe-adversary`.
**Produce:** failing cases with the exact state that triggered them.

### Route F — "What changed upstream?" / "find new opportunities"
→ `llms.txt` (upstream index) vs `reference/` (our mirror) to diff
→ `knowledge/MASTERY.md` §11
**Own it:** `typesafe-scout`.
**Produce:** a diff report, or ranked candidate surfaces.

### Route G — "I just need to make one call, now"
Read nothing else. Copy this:

```python
import os, sys
KIT = os.environ.get("TYPESAFE_KIT", ".")
sys.path.insert(0, f"{KIT}/lib")
from typesafe_client import TypeSafeClient, choice, score, noul

client = TypeSafeClient()                      # reads TYPESAFE_API_KEY (env, or .env in the kit root)
r = client.ask(
    state={"doc": "..."},                      # only what the questions need
    questions={
        "kind":  choice("What kind of document is `doc`?",
                        {"invoice": "A bill requesting payment",
                         "receipt": "Proof of completed payment",
                         "other": None}),           # always offer an escape option
        "urgent": noul("Does `doc` state a deadline within 7 days?"),
        "detail": score("How complete is `doc`?",
                        ["Missing required fields",      # levels describe SITUATIONS
                         "Present but unverified",
                         "Complete and internally consistent"]),
    },
    tag="your-surface-name",                   # attributes spend in the usage log
)
r.choice("kind"); r.confidence("kind"); r.noul("urgent"); r.normalized_score("detail")
```

---

## The eight rules (violating these is how builds fail)

1. **Batch questions over a shared state into one request.** They run in parallel and cost only
   their own tokens. Include speculative ones you may discard — measured 12.2x cheaper, 10.0x
   faster than separate calls, with answers provably unchanged.
   *Exception:* when each candidate needs its **own** state, fan out instead (the reranking
   cookbook runs 1,200 separate Nouls). Batching shares tokens; with nothing shared, nothing saves.
2. **Decompose ruthlessly.** The docs' own "most important concept". Replace `is_spam?` with six
   atomic Nouls and weight them in code. Broad questions hide judgments you cannot tune.
3. **Match the primitive to what your code does with the answer.** A Choice settles *which*; a
   Noul settles *whether at all*. They do not share a scale — see §9, measured live.
4. **Score levels describe situations, never degrees.** "Moderately severe" gives the model
   nothing. Bare numbers collapse accuracy (0.57 @ 0.35 confidence vs 0.0 @ 1.0 with
   descriptions). Normalize by `len(levels)-1` before combining scales.
5. **Select, don't generate.** Enumerate candidates in code; let Jev pick. It cannot choose a
   value you failed to offer — so over-generate candidates and let the judgment supply precision.
6. **Keep policy in code.** Weights and thresholds live where you can read and change them.
   Changing a weight then needs no re-inference.
7. **Every threshold is a hypothesis until measured.** Use `QuestionSet(gates_irreversible=True)`;
   it refuses to run while `validated_on` is None.
8. **Pin the model version** wherever a threshold exists. `jev-latest` moves on release.

---

## Repo map

| Path | What | When to open it |
|---|---|---|
| `AGENTS.md` | This file | Always first |
| `knowledge/MASTERY.md` | Distilled reference, 11 sections | Per the routes above — read *sections*, not the file |
| `knowledge/cookbooks-extraction.md` | Select-don't-generate, cascades, verification | Route B, extraction work |
| `knowledge/cookbooks-retrieval.md` | Ranking, routing, taxonomies, **agent/skill routing blueprint** | Route B, routing work |
| `knowledge/cookbooks-reliability.md` | Guardrails, self-consistency, batching economics | Routes D, E |
| `lib/typesafe_client.py` | The client — pinned model, validation, retry, usage log | Route C |
| `lib/questions/__init__.py` | `QuestionSet`: questions + thresholds + provenance | Route C, D |
| `examples/` | Synthetic roster + labelled data the tools and cookbooks reference | Route B, C, D |
| `reference/` | Upstream doc pages, fetched by `scripts/fetch-docs.sh` | Only when a route sends you, or a detail is contested |
| `tests/` | Live smoke tests | Route C, as a worked example |

**Precedence when sources disagree:** `reference/` (upstream text) > `knowledge/` (our
distillation) > this file. If you find a contradiction, fix the derived file and say so.

---

## Working agreements

- **Use the client.** Never hand-roll the HTTP call — you lose pinning, validation, retry, and
  spend logging. If the client lacks something, extend it rather than bypassing it.
- **Tag every call** with your surface name so spend stays attributable
  (`$TYPESAFE_USAGE_LOG`, default `~/.typesafe/usage.jsonl`).
- **Test against the live API.** Once `TYPESAFE_API_KEY` is set, probe rather than speculate —
  a design that has never been run is a hypothesis.
- **Never invent a threshold.** If you need one and have no labelled data, say so and hand to
  `typesafe-calibrator` rather than picking a plausible-looking number.
- **State is untrusted.** Jev does not treat state as hostile by default. Anywhere it reads
  scraped, user-supplied, or model-generated text, say so and involve `typesafe-adversary`.
- **Report cost and latency** with any result. Both are observable; unmeasured claims are not.
- **Write findings back.** A measured threshold, a new failure mode, or a working pattern
  belongs in `knowledge/`, not just in your final message.
