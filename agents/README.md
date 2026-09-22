# The TypeSafe guild

Six Claude Code subagents you can launch from any repo. They live globally in
`~/.claude/agents/` but they all come back to the kit root (this repo) to learn how to build.

**Every one of them reads `../AGENTS.md` first.** That file is the portal: a 60-second
orientation, the fit test, the eight rules, and a routing table that sends each agent to the
*sections* it needs rather than whole files. Nobody loads 2,000 lines to answer one question.

## Who does what

| Agent | Owns | Produces | Model |
|---|---|---|---|
| `typesafe-scout` | Finding where TypeSafe belongs; upstream drift | Ranked candidate surfaces | sonnet |
| `typesafe-architect` | The shape: code vs judgment, request topology | A design, not code | opus |
| `typesafe-question-smith` | The craft: decomposition, primitives, criteria, levels | The `questions` dict | opus |
| `typesafe-engineer` | Implementation, composition, live tests | Registered `QuestionSet` + code | sonnet |
| `typesafe-calibrator` | Thresholds from labelled data, self-consistency | Measured gates + `validated_on` | sonnet |
| `typesafe-adversary` | Red-teaming before users | Failing cases with exact state | opus |

Each role exists because of a specific way builds fail without it:

- no **scout** → the technology sits unused and the mirror silently rots
- no **architect** → LLM-shaped thinking; an agent loop where a workflow belongs
- no **question-smith** → broad questions hiding judgments; degree-words as Score levels
- no **engineer** → the model doing arithmetic; policy buried in prompt text
- no **calibrator** → invented thresholds (this repo's own tests caught exactly that)
- no **adversary** → a router that untrusted text can steer

## Each specialist ships a tool

Three of these agents built a tool that carries their judgment to anyone who needs it, without
a spawn. They live in `tools/` and are documented in `../AGENTS.md`.

| Tool | Carries | Built by |
|---|---|---|
| `question_critic.py --file d.json` | Whether a drafted question is any good | `question-smith` |
| `jaggedness_screen.py --file d.json` | Which of the nine failure modes a design risks | `adversary` |
| `consistency_probe.py --set X --inputs f` | Whether answers are stable or only look stable | `calibrator` |

A tool does not replace its author. The screen flags exposure at design time; it does not
replace `typesafe-adversary` actually probing what you built. The probe ranks instability; it
does not replace `typesafe-calibrator` measuring accuracy against labels. **Reach for the tool
first** — it is a few cents and a second — and spawn the specialist when the tool says
something needs real judgment.

The tools also audit each other. The jaggedness screen found a composition defect in the
architect's router design that the design's own risk section missed; the consistency probe then
found the least-stable question inside the jaggedness screen. Run them on your own work before
you hand it on.

## The chain

```
                  scout ──► architect ──► question-smith ──► engineer
                                                │                │
                                                ▼                ▼
                                          calibrator ◄──────────┘
                                                │
                                                ▼
                                           adversary
                                                │
                              ┌─────────────────┴─────────────────┐
                              ▼                                   ▼
                       question-smith                          engineer
                      (question bugs)                       (code bugs)
```

Not every task walks the whole chain. A one-off classification may be `question-smith` →
`engineer`. Anything that gates an irreversible action must reach `calibrator`. Anything that
reads untrusted text must reach `adversary`.

## Orchestrator routing

Pick by the shape of the request, not its vocabulary:

| Request sounds like | Start with |
|---|---|
| "could we use AI for…", "what could Jev do here" | `typesafe-scout` |
| "design a decision for…", "should this be TypeSafe" | `typesafe-architect` |
| "write the questions", "this returns low confidence" | `typesafe-question-smith` |
| "implement it", "wire it into this service" | `typesafe-engineer` |
| "what cutoff", "is this reliable enough to ship" | `typesafe-calibrator` |
| "is this safe", "break it", "injection" | `typesafe-adversary` |
| "what changed upstream" | `typesafe-scout` |

When unsure, start with `typesafe-architect` — it is the one that will tell you TypeSafe is the
wrong tool, which is a common and useful answer.

In Claude Code, pick the agent by launching it with the Agent tool's `subagent_type` (or let the
orchestrator route by request shape, per the table above).

## Shared discipline

Every agent in this guild enforces the same non-negotiables, so a handoff never loses them:

- **Code owns the workflow; Jev only judges.** No arithmetic, counting, date comparison, or
  generation crosses to the model side.
- **Batch questions over a shared state into one request** — including speculative ones. Fan out
  only when each candidate needs its own state.
- **A Choice is relative, a Noul is absolute.** Never port a threshold between them.
- **Every threshold is a hypothesis until measured** on real data and recorded in `validated_on`.
- **Pin `jev-1.13.0`** wherever a threshold exists.
- **Test against the live API.** Once `TYPESAFE_API_KEY` is set, a design that has never run is
  still a hypothesis — run it.
- **Write findings back** into `knowledge/` — a measured threshold or a new failure mode is
  worth more to the next agent than to the current message.

## Adding an agent

Keep the mandate sharp and non-overlapping — overlap is how handoffs get dropped. A new agent
needs: frontmatter matching the Claude Code agent frontmatter schema (`name`, `description`,
`model`, `color`, `category: typesafe`, `default_mode`, `triggers`, `handoff_from`/`handoff_to`),
a **Home base** section naming its route into the repo, explicit **Boundaries**, and an
**Output** contract. Then copy or symlink it into `~/.claude/agents/`.
