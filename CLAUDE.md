# typesafe-claude-kit

A Claude Code kit for building decision layers on TypeSafe's Jev model: typed Choice / Score /
Noul judgments with calibrated probabilities, consumed directly by code.

**Core rule: code owns the workflow, the model only judges.** Jev never generates text, writes
code, or picks a next action — it answers one bounded, typed question at a time.

## Where to start reading

1. `AGENTS.md` — the portal: 60-second orientation, the fit test, standing rules, routing table.
   Read this before anything else; it sends you to *sections*, not whole files.
2. `knowledge/MASTERY.md` — the distilled reference, read by section per the routing table.
3. `reference/` — the full upstream doc mirror, fetched (not vendored) by
   `scripts/fetch-docs.sh`. Run that once before relying on it; it's gitignored.

## Running things

```bash
./setup.sh                        # first-time setup: deps, agents/skill, .env, docs mirror
python3 tests/run_all.py --list   # see what suites exist, no API calls
python3 tests/run_all.py          # run everything live (needs TYPESAFE_API_KEY)
python3 tools/question_critic.py --file draft.json          # critique a drafted question
python3 tools/jaggedness_screen.py --file design.json        # screen a design for failure modes
python3 tools/consistency_probe.py --set ticket_triage --inputs examples/labels/ticket_triage.json
```

## Environment

| Variable | Required | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | Yes | none — env var, or `.env` at the kit root |
| `TYPESAFE_USAGE_LOG` | No | `~/.typesafe/usage.jsonl` |
| `TYPESAFE_KIT` | No | `.` — set if agents run from outside the clone |
| `TYPESAFE_AGENT_ROSTER` | No | `examples/agent-roster.json` |

## Never send Jev

Arithmetic · counting (characters, occurrences, list items) · date/time comparison or ordering ·
text generation. These are documented failure modes (`reference/model-jaggedness/jev-1.13.md`),
not edge cases. Also don't assume `P(x) == 1 - P(not x)`, and never port a threshold between a
Noul and a Choice — they don't share a scale.

## Which agent to reach for

| Need | Agent |
|---|---|
| Find where TypeSafe fits, or check for upstream drift | `typesafe-scout` |
| Decide the shape: code vs. judgment, request topology | `typesafe-architect` |
| Write or fix the actual questions | `typesafe-question-smith` |
| Implement: QuestionSet, client wiring, live tests | `typesafe-engineer` |
| Turn an invented threshold into a measured one | `typesafe-calibrator` |
| Red-team a design before it ships | `typesafe-adversary` |

When unsure, start with `typesafe-architect` — telling you TypeSafe is the wrong tool is a
valid, common answer. Details and the tool each specialist built: `agents/README.md`.

## Checklist before shipping an integration

- Every question is one judgment, phrased literally; levels describe situations, not degrees
- All questions for one piece of state batched into a single request
- No math, counting, dates, or generation sent to the model
- Thresholds are named in code and validated on real data before gating anything irreversible
- Model version pinned (`jev-1.13.0`) wherever a threshold exists
- Adversarial inputs tested if the state contains untrusted text
