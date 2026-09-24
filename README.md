# typesafe-claude-kit

A Claude Code-native kit for building System-One decision layers on [TypeSafe](https://typesafe.ai)'s
Jev model: typed **Choice / Score / Noul** judgments with calibrated probabilities that your
code consumes directly. It sits *underneath* an orchestrating LLM as a cheap, fast, inspectable
decision layer — never for text generation, arithmetic, counting, or date math.

This is not the TypeSafe SDK. The SDK lives upstream at
[api.typesafe.ai](https://api.typesafe.ai) / [docs.typesafe.ai](https://docs.typesafe.ai). This
kit is the Claude Code layer on top: a guild of subagents, a skill, a thin client, worked
QuestionSets, and the tools that make thresholds measured instead of invented.

## The short version

Claude is the friend who writes, plans, and reasons. Jev is a fast, cheap referee that only
answers multiple-choice questions — and tells you how sure it is.

Hand it a support ticket and a form: *which team? how severe? are they asking for a refund?*
It ticks the boxes and attaches a probability to each (`team="billing"`, `refund=0.97`). It
never writes a sentence back, so there is nothing to parse.

It has three kinds of question:

| Primitive | Asks | Returns |
|---|---|---|
| **Choice** | Which one of these? | the winning option + a probability per option |
| **Score** | Where on this scale? | one of 2–10 levels *you describe in words* |
| **Noul** | Is this true? | a single probability, 0 to 1 |

Your code stays the boss. Jev judges; code decides:

```python
if result.noul("refund_requested") > 0.9 and result.choice("team") == "billing":
    auto_route_to_billing()
else:
    ask_a_human()
```

The probabilities are calibrated: across many calls, the "90% sure" answers are right about
90% of the time. That makes thresholds meaningful, but it does not make any single answer
correct, so scale the bar with the risk of the action. The calibrator agent and
`tools/confidence_accuracy_curve.py` exist to measure that bar instead of guessing it.

**Why bother:** $0.042 per million input tokens, output free, ~300–600ms per call — and many
questions batch into one call (about 11× cheaper than asking them separately).

**Never send it:** arithmetic, counting, date comparison, text generation, multi-hop
"the thing that the thing points to" reasoning, or huge padded state. These are documented
failure modes, not edge cases. A lookup belongs in code, prose belongs to Claude, choosing
the next step in a loop belongs to an agent — Jev is for the judgment calls in between.

**Where to start in Claude Code:** after setup, say *"use typesafe-scout to find where Jev fits
in this repo"* and let the guild take it from there.

## What's inside

```
.
├── AGENTS.md              # portal: 60-second orientation, fit test, routing table — read first
├── CLAUDE.md              # what a Claude Code session opened here should know
├── agents/                # 6 Claude Code subagents (the guild) + agents/README.md
├── skills/typesafe/       # the /typesafe skill
├── lib/
│   ├── typesafe_client.py # thin HTTP client: pinned model, validation, retries, usage log
│   └── questions/         # QuestionSet convention + 3 example sets
├── tools/                 # 5 quality/calibration tools, each usable standalone
├── knowledge/             # MASTERY.md + 3 cookbooks, distilled from docs.typesafe.ai
├── examples/              # fixtures + reddit_guide/ (a subreddit guide Jev picks, never writes)
├── tests/                 # live test suites + run_all.py aggregator
├── scripts/fetch-docs.sh  # mirrors docs.typesafe.ai into reference/ (gitignored)
├── .env.example
└── setup.sh
```

## Quickstart

```bash
git clone https://github.com/herakles-dev/typesafe-claude-kit.git
cd typesafe-claude-kit
cp .env.example .env        # set TYPESAFE_API_KEY (get one at https://typesafe.ai)
./setup.sh                  # installs deps, links the guild + skill, mirrors the docs
scripts/fetch-docs.sh       # (setup.sh already does this; re-run any time to refresh)
python3 tests/run_all.py --list
```

A minimal call, using the shipped `ticket_triage` QuestionSet:

```python
import os, sys
sys.path.insert(0, os.environ.get("TYPESAFE_KIT", ".") + "/lib")
from typesafe_client import TypeSafeClient
from questions import get

client = TypeSafeClient()          # key from TYPESAFE_API_KEY env var, or .env
ticket_triage = get("ticket_triage")
result = ticket_triage.ask(client, {"ticket": "Charged twice this month, please refund."})

print(result.choice("team"), result.confidence("team"))
print(result.score("severity"), result.noul("refund_requested"))
```

## The guild

Six Claude Code subagents, meant to be launched from any repo, in pipeline order:

| Agent | Owns |
|---|---|
| `typesafe-scout` | Finding where TypeSafe belongs; upstream doc drift |
| `typesafe-architect` | The shape of a design: code vs. judgment, request topology |
| `typesafe-question-smith` | The questions themselves: decomposition, primitives, criteria, levels |
| `typesafe-engineer` | Implementation: registered QuestionSets, client wiring, live tests |
| `typesafe-calibrator` | Turning invented thresholds into measured ones |
| `typesafe-adversary` | Red-teaming a design before users do |

Install them with `./setup.sh` (symlinks by default; `--copy` to copy instead), or by hand:
copy `agents/typesafe-*.md` into `~/.claude/agents/` and `skills/typesafe/` into
`~/.claude/skills/typesafe/`. Then ask Claude Code to use one, e.g. "use typesafe-architect to
design this". Full routing detail, the failure mode each role prevents, and the tools each one
built: [`agents/README.md`](agents/README.md).

## Tools

Each tool is a standalone script — a few cents and a second, cheaper than spawning the
specialist that would otherwise answer the same question.

| Tool | Answers | Example |
|---|---|---|
| `tools/question_critic.py` | Is a drafted question any good, and what would fix it | `python3 tools/question_critic.py --file draft.json` |
| `tools/jaggedness_screen.py` | Which of the nine documented failure modes a design risks | `python3 tools/jaggedness_screen.py --file design.json` |
| `tools/consistency_probe.py` | Are answers stable across repeats, or only look stable | `python3 tools/consistency_probe.py --set ticket_triage --inputs examples/labels/ticket_triage.json` |
| `tools/question_health.py` | Does a question carry any information at all on these inputs | `python3 tools/question_health.py --set ticket_triage --inputs examples/labels/ticket_triage.json` |
| `tools/confidence_accuracy_curve.py` | Where accuracy actually degrades, and what each cut costs | `python3 tools/confidence_accuracy_curve.py --set ticket_triage --labels examples/labels/ticket_triage.json --answer team --expected expected_team` |

## Example: a guide written by a model that can't write

[`examples/reddit_guide/`](examples/reddit_guide/) turns a subreddit's comments into a field
guide without generating a word: code splits comments into sentences, Jev judges and picks,
every line links back to the real comment. The reference run read 7,728 sentences from
r/ClaudeCode in 118 seconds for $0.21 — [results](examples/reddit_guide/results/2026-09-24-ClaudeCode.md).

## Knowledge

Start at [`knowledge/MASTERY.md`](knowledge/MASTERY.md) — the distilled reference. Then the
cookbooks for worked patterns by domain: `cookbooks-extraction.md`, `cookbooks-retrieval.md`,
`cookbooks-reliability.md`.

`reference/` (the full upstream doc mirror) is fetched, not vendored: TypeSafe's docs change
without notice, and re-fetching keeps this kit honest instead of shipping a copy that quietly
goes stale. Run `scripts/fetch-docs.sh` once to populate it.

## Tests

Every suite in `tests/` makes live calls against the TypeSafe API — cheap (well under a cent
per suite) but not free, and `TYPESAFE_API_KEY` must be set.

```bash
python3 tests/run_all.py --list      # see what would run, no API calls
python3 tests/run_all.py             # run everything
python3 tests/run_all.py --only client --json
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for adding a QuestionSet, adding an agent, and the test
workflow.

## Using with Claude Code

This repo ships a `CLAUDE.md` that gives Claude Code full context on first open — what the kit
is, where to start reading, and which agent to reach for.

```bash
claude    # start Claude Code in this directory
```

## License

MIT — see [LICENSE](LICENSE)
