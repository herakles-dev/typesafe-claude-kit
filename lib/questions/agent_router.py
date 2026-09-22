"""Stage-1 questions for agent routing: which agent, if any, should be assigned a task.

This module owns stage 1 only -- one request over the task text that produces (a) a ranking
of the whole spawnable roster and (b) the gates that decide whether a second, narrower request
fires at all.

`gates_irreversible=True` and `validated_on=None` are load-bearing, not bookkeeping: the
answers here select an agent that may hold Write, Edit, and Bash on the repository. The set
refuses to run until `typesafe-calibrator` measures the cutoffs in `REQUIRED_THRESHOLDS`
against labelled tasks.

--- The need gate, and why it is one Score --------------------------------------------------

The design originally carried three gate Nouls (`needs_domain_depth`,
`follows_established_playbook`, `generalist_suffices`) combined in code as a mean of oriented
values, with the third inverted as `1 - noul`. That composition is withdrawn. Two separate
defects, both documented:

  * `1 - noul` assumes `P(not x) == 1 - P(x)`. Measured false: a question and its negation
    summed to 1.19, not 1.0 (`MASTERY.md` section 8, failure mode #8). The inverted term is
    therefore not the same quantity as a directly-worded positive Noul, and no amount of
    calibration recovers what it is.
  * Even the two same-oriented Nouls do not share a scale. Averaging three numbers drawn from
    three uncalibrated scales yields a number whose only defined meaning is "whatever the
    threshold was fitted to" -- and fitting it hides which of the three questions drove a
    misfire, which is exactly the diagnostic a gate needs most.

Alternatives weighed, and why this one won:

  * *Three independent gates, three thresholds.* Triples the labelling burden for one decision
    and still needs an uncalibrated boolean rule to combine them. AND rejects a task that needs
    deep expertise but follows no procedure; OR is `max` with a step function.
  * *`max` instead of `mean`.* Right about the structure -- "needs a specialist" is a
    disjunction, and `max` neither dilutes a strong lone signal nor requires the invalid
    inversion. But it still compares uncalibrated scales, so it returns whichever question runs
    systematically hottest on this population rather than whichever signal is strongest. And
    the extraction cookbook's reason for aggregating hazards with `max` does **not** transfer:
    there, `max` biases toward firing, and firing is the safe direction. Here the design's own
    asymmetry runs the other way -- a wrong launch spawns a write-capable agent, a miss costs
    the orchestrator doing the work itself -- so a bias toward firing is a bias toward the
    expensive error. The mechanism transfers; the justification inverts.
  * *One well-worded Noul.* One clean absolute number, but it restores the broad question
    `MASTERY.md` section 6 step 4 forbids, and a Noul returns no confidence and no
    distribution -- nothing to inspect when it is wrong.

So: one Score, `need::work_specificity`, four levels each describing a situation. Levels inside
one question are judged against one another's descriptions rather than across question
wordings, so the scale is internally consistent by construction. **What the number means:**
`normalized_score("need::work_specificity")` is the probability-weighted position of this task
on a 0-3 scale whose four points are named descriptions of how specialised the knowledge
required to do the work is, rescaled to 0-1. It is not a probability of anything, and it must
never be compared against a Noul.

`follows_established_playbook` survives as `diagnostic::follows_established_playbook`: it rides
free in the same batch, feeds nothing, and exists so the calibrator can test the measured
cookbook prior (three angles beat one) against labelled data without a second measurement pass.
The `diagnostic::` prefix is there so nobody composes it by accident.

--- Roster ---------------------------------------------------------------------------------

`which_wide`'s options are resolved at call time, never from a snapshot: the design records
verified drift between the registry, the agent files on disk, and the launcher's actually
spawnable set. `stage1_questions(roster)` builds the Choice; the module-level set carries a
registry snapshot for critique and calibration only.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Mapping

from . import QuestionSet, register
from typesafe_client import Result, TypeSafeClient, choice, noul, score

__all__ = [
    "AGENT_ROUTER_STAGE1",
    "REQUIRED_THRESHOLDS",
    "GATE_QUESTIONS",
    "load_registry_roster",
    "which_wide",
    "stage1_questions",
    "ask_stage1",
]

#: Env-overridable so a caller can point this at their own roster snapshot without editing
#: code. Defaults to the example roster shipped with this kit.
REGISTRY_PATH = Path(
    os.environ.get(
        "TYPESAFE_AGENT_ROSTER",
        str(Path(__file__).resolve().parents[2] / "examples" / "agent-roster.json"),
    )
)

#: Named cutoffs this set needs and deliberately does not have. Each says what crossing it
#: means, so `typesafe-calibrator` measures a stated hypothesis rather than inventing one.
#: They read different primitives on different scales and none of them may be ported to
#: another (`MASTERY.md` section 9, reproduced live).
REQUIRED_THRESHOLDS: tuple[tuple[str, str], ...] = (
    (
        "NEED_ABOVE",
        "Read against `normalized_score('need::work_specificity')`. Crossing it means: the "
        "work looks more like it turns on the particulars of one system than like ordinary "
        "generalist engineering, so spend a second request and consider assigning. Below it, "
        "emit the none-value with reason `no-specialist-needed` and make no second call.",
    ),
    (
        "HANDOFF_ABOVE",
        "Read against `noul('handoff::self_contained')`. Crossing it means: the task text "
        "settles enough that an agent could finish on it alone. Below it, emit the none-value "
        "with reason `task-underspecified` -- a different signal from the need gate, inviting "
        "the orchestrator to split or re-specify rather than absorb the work.",
    ),
    (
        "ACTIONABLE_ABOVE",
        "Read against `noul('gate::agent_actionable')`. Below it, no agent can finish this "
        "work whatever the roster says: emit the none-value with reason `not-agent-work` and "
        "make no second call. A third distinct signal -- a task can be well specified and "
        "deeply specialised and still need a person.",
    ),
    (
        "AUTO_ASSIGN_MAX_BLAST",
        "Read against `normalized_score('blast_radius')`. At or above it the router may "
        "record which agent should be assigned but must not trigger an unattended spawn. This "
        "restates existing risk-tiering policy; it is not a new one. Since the levels were "
        "rewritten as the arrangements themselves, the default rule needs no number: round "
        "`score('blast_radius')` to the nearest level and the level names what to arrange, "
        "with levels 2 and 3 both meaning no unattended spawn. Measure this cutoff only if a "
        "caller wants a cut that round-to-nearest does not give -- and if so, measure it, "
        "because both boundaries sit where real tasks cluster.",
    ),
)


# --- Roster-independent questions ------------------------------------------------------------

GATE_QUESTIONS: dict[str, dict] = {
    "need::work_specificity": score(
        {
            "question": (
                "How specialised is the knowledge needed to do the work described in `task` "
                "correctly?"
            ),
            "context": (
                "`task` is a unit of work about to be handed to an autonomous coding agent. "
                "The platform keeps a roster of agents that each concentrate on one "
                "technology, system, or practice, alongside general-purpose agents that hold "
                "the same tools but no particular concentration."
            ),
            "focus": (
                "Judge only what doing the work demands of whoever does it. Do not judge how "
                "large the task is, how risky it is, how clearly it is written, or whether "
                "the platform happens to keep an agent for it."
            ),
        },
        [
            "No knowledge beyond the task text itself is needed. The change is mechanical: "
            "renaming, moving, reformatting, updating a value, or repeating a pattern already "
            "present in the repository.",
            "Only general engineering knowledge is needed, of the kind a competent generalist "
            "already carries: a widely used language, framework, or command-line tool, with "
            "public documentation to hand if anything needs checking.",
            "Particulars the worker must already hold are needed: the conventions of one "
            "named system, service, protocol, or file format, or the usual order of steps for "
            "this kind of change. Someone without them would have to go and learn them before "
            "starting.",
            "Sustained expertise in one field is needed -- security review, database "
            "performance, cryptography, statistical inference, distributed-systems failure "
            "modes -- of the depth where a capable generalist's first attempt would plausibly "
            "be wrong in ways they would not notice.",
        ],
    ),
    "handoff::self_contained": noul(
        {
            "question": (
                "Could an agent handed only the text in `task`, plus access to the repository "
                "`task` names, carry the work through to completion without first coming back "
                "to the orchestrator to ask what was meant?"
            ),
            "focus": (
                "Judge only whether the text settles what is being asked for. An agent may "
                "read the repository, search it, and run its tests; needing to do that is not "
                "coming back to ask. Do not judge how hard the work is, how long it would "
                "take, or whether the agent would get it right."
            ),
            "examples_of_true": [
                "Add a /healthz endpoint to the billing service returning 200 and the build "
                "SHA, with the path and response named in the acceptance criteria.",
                "Pin the base image in every Dockerfile under services/ to a digest.",
            ],
            "examples_of_false": [
                "Finish what we discussed and apply the same fix to the other services.",
                "Make the dashboard better.",
                "Do the part of the migration that the last agent did not get to.",
            ],
        },
        {
            "true": "The text settles what is being asked for; work could start and finish on "
                    "it alone.",
            "false": "Something the text leaves open would have to be settled by the "
                     "orchestrator before the work could finish -- a target named only by "
                     "reference to an earlier conversation, a choice the text does not make, "
                     "or a goal with no stated end state.",
        },
    ),
    # Levels are the arrangement the orchestrator has to make, not an abstract reach-or-undo
    # scale -- the entity-alignment recipe (`cookbooks-extraction.md` section 5), where the
    # nearest level *names the outcome* and no threshold is fitted.
    #
    # Measured against the previous "how hard would the result be to undo?" wording on twelve
    # real tasks spanning read-only to irreversible: round-to-nearest agreed with the
    # hand-written expectation 10/12 against 8/12. Both corrections are the same pathology --
    # repository-only work read as live-service work, because a file that a live service reads
    # is not a live service. "Add a /healthz endpoint" 1.91 @ 0.91 -> 1.32 @ 0.66, and "update
    # the port allocation in config.json" 1.87 @ 0.86 -> 1.33 @ 0.64; both were landing
    # on level 2 ("a further deliberate operation") and now land on level 1 ("look over the
    # diff"), which is what the orchestrator actually has to do. Mean confidence falls 0.88 ->
    # 0.82, all of it on those two cases, where a split distribution is the honest answer.
    #
    # `question_critic` scores this 0.49 on `levels_multi_dimensional`, slightly worse than the
    # 0.42 it gave the old wording. That flag was chased through three rewrites and it is a
    # false positive on this question family: a control Score asking *only* about undo cost,
    # with every mention of reach surgically removed, still scores 0.43-0.46, while a control
    # asking only about reach scores 0.19. All five readings are stable to +-0.03 over three
    # runs. The critic is reading "what operation, against what, leaving what trace" as several
    # dimensions; it is one. Judge this question on the twelve tasks, not on that number.
    "blast_radius": score(
        {
            "question": (
                "Before an agent is set to work on `task`, which of these does the "
                "orchestrator have to arrange, given how the result would be taken back if "
                "the work went badly?"
            ),
            "context": (
                "`task` is about to be handed to an agent that may hold file-editing and "
                "shell access. Exactly one of the listed arrangements is the least the "
                "orchestrator can get away with for this work."
            ),
            "focus": (
                "Choose by what it would take to put things back as they were, not by how "
                "likely the work is to go badly, how important it is, or how much skill it "
                "needs."
            ),
        },
        [
            "Let the agent run and read whatever it produces. Nothing has to be arranged, "
            "because nothing outside the conversation changes: the work examines code, logs, "
            "documents, or metrics and writes up what it found.",
            "Let the agent run, then look over what it changed before anything is merged or "
            "deployed. Nothing has to be arranged in advance, because until a person moves "
            "the work on, everything it touched sits in a working tree and discarding it "
            "leaves no trace.",
            "Have a rollback ready before the agent starts, and a person to approve the "
            "change reaching a live system. Putting things back takes a further deliberate "
            "operation -- a redeploy, a configuration rollback, a restore from backup -- and "
            "until that runs, a live service behaves differently than it did before.",
            "Do not hand this to an agent to carry out on its own; a person carries it out or "
            "watches each step. There is nothing to put back afterwards: what was there is "
            "destroyed or overwritten with no retained copy, or the result has already left "
            "the platform and cannot be recalled.",
        ],
    ),
    # Added after a live probe: routing "negotiate the AWS enterprise agreement renewal"
    # against the real roster returned `need::work_specificity` 0.75 and a confident-looking
    # `contracts-negotiator` pick, because contract negotiation genuinely is specialised
    # knowledge. Neither existing gate asks whether the work is the kind a software agent can
    # finish at all -- that is a third, independent dimension, and without it the "nothing
    # applies" class only gets caught at stage 2, after a second request has already been paid
    # for. Absolute by construction, so unlike the Choice it can say "none of this works".
    #
    # The capability-not-permission clause in `focus` and the two `examples_of_true` were added
    # after the first draft read "production Postgres" and "certbot" as human-authority work:
    # drop-a-production-table 0.34 -> 0.77, add-an-nginx-site 0.66 -> 0.84, while the true
    # negatives held (negotiate-with-AWS 0.03 -> 0.04). Held-out cases that resemble nothing in
    # the examples: interview candidates 0.10, photograph a server rack 0.11, fix a flaky test
    # 0.89, rotate a database credential 0.76. That is evidence the question discriminates; it
    # is not a threshold, and ACTIONABLE_ABOVE stays unset.
    "gate::agent_actionable": noul(
        {
            "question": (
                "Could an agent working on its own -- with the repository `task` names, a "
                "shell, and web search -- carry the work described in `task` through to "
                "completion?"
            ),
            "focus": (
                "Judge whether this is the kind of work a software agent can finish by "
                "itself. Work that needs a person to deal with someone outside the system, to "
                "use an account or credential the agent does not hold, or to exercise an "
                "authority reserved to a person cannot be finished by an agent however well "
                "it is described. Judge capability, not permission: work an agent is "
                "technically able to carry out counts as finishable even where a rule says a "
                "person must approve it first, and even where the consequences are severe. Do "
                "not judge how well specified the task is, how much expertise it demands, or "
                "whether the platform happens to keep an agent for it."
            ),
            "examples_of_false": [
                "Negotiate next year's pricing with the vendor's account team.",
                "Get the client to sign the statement of work.",
                "Decide whether to take the funding round.",
            ],
            "examples_of_true": [
                "Drop a deprecated table from the production database and remove its model "
                "class -- destructive, and a rule may require sign-off, but an agent with a "
                "shell can carry it out.",
                "Issue a TLS certificate for a new subdomain using the certbot setup already "
                "configured on the host.",
            ],
        },
        {
            "true": "An agent working alone could take this to a finished state.",
            "false": "Finishing requires a person: someone must deal with a party outside the "
                     "system, hold an authority or credential the agent does not, or make a "
                     "decision reserved to a person.",
        },
    ),
    # Rides free in this batch. Feeds nothing. Kept so the calibrator can test the cookbook's
    # measured prior -- that several angles beat one -- against labelled tasks, without
    # running a second measurement pass to get the feature back.
    "diagnostic::follows_established_playbook": noul(
        {
            "question": (
                "Would someone experienced at this kind of work carry out `task` by following "
                "an established procedure for it, rather than working the steps out from what "
                "they find in the repository?"
            ),
            "focus": (
                "Judge the kind of work, not how this particular description is written. A "
                "procedure counts whether it is written down in a runbook, carried in a "
                "tool's own conventions, or simply the known order of steps for this kind of "
                "change."
            ),
        },
        {
            "true": "This kind of work has a usual order of steps that an experienced person "
                    "would follow.",
            "false": "Each instance of this kind of work is reasoned out from the situation in "
                     "front of you; there is no usual order of steps to follow.",
        },
    ),
}


# --- The roster Choice -------------------------------------------------------------------------


def load_registry_roster(path: Path | str = REGISTRY_PATH) -> dict[str, str]:
    """Active agents from the registry, name -> `description` verbatim.

    A snapshot. Production callers pass the launcher's actually spawnable set instead; the
    design records verified drift in both directions between registry, disk, and launcher.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        name: entry["description"]
        for name, entry in sorted(data.get("agents", {}).items())
        if entry.get("status", "active") == "active" and entry.get("description")
    }


def which_wide(roster: Mapping[str, str]) -> dict:
    """The stage-1 Choice over the whole roster.

    Option criteria are the registry `description` **verbatim** -- not rewritten, not enriched.
    That is deliberate: it keeps the router's informational position identical to the
    orchestrator's, so any improvement comes from being asked rather than from being told more.

    No escape option, and the structural warning that provokes is answered in the instruction
    rather than by adding one. This Choice is a **ranker**: code takes the top K and never acts
    on the winner alone. Whether to assign anything at all is settled by the gate Nouls here and
    by the per-finalist `fits::` Nouls at stage 2 -- all absolute questions, which is the only
    kind that can say "none of these".

    Measured rather than assumed (2026-09-20, live, 103-option variant with
    `none_of_these: "No registered agent does the kind of work this task asks for."`):

      | task                              | no escape                  | with escape                |
      |-----------------------------------|----------------------------|----------------------------|
      | rename a helper (agent exists)    | top-3 identical, conf 0.94 | top-3 identical, conf 0.94 |
      | underspecified dashboard task     | top-3 identical, conf 0.54 | escape 0.010, conf 0.62    |
      | negotiate an AWS contract (none)  | contracts-negotiator 0.50  | escape 0.300, next 0.280   |

    So the escape option does **not** distort the shortlist -- an earlier draft of this
    docstring claimed it would, and the measurement does not support that. What it does is
    dilute: on the one task where "none" is unambiguously right, the escape reaches only 0.30
    against 0.50 of mass still spread over wrong agents, because a forced distribution over 103
    options has no way to concentrate on abstention. Omitting it is therefore about weakness,
    not distortion, and the abstention it cannot provide is what `gate::agent_actionable` and
    the stage-2 `fits::` Nouls exist for. Note also that the escape stayed near zero (0.010) on
    the underspecified task: roster coverage and specification quality are different questions,
    and only the latter's own gate catches it.
    """
    if not roster:
        raise ValueError("Roster is empty; resolve the spawnable set before calling")
    return choice(
        {
            "question": (
                "Which one of these agents should be assigned to carry out the work described "
                "in `task`?"
            ),
            "context": (
                "Each option is a registered agent in your agent roster. Its text is the "
                "one-line description the orchestrator itself reads when it picks an agent by "
                "hand -- nothing has been added to it."
            ),
            "focus": (
                "Match the work `task` asks for against what each description says the agent "
                "is for. Several agents carry similar names and overlapping subject matter; "
                "judge what the description says the agent does, not which name shares the "
                "most words with `task`."
            ),
            "how_this_answer_is_used": (
                "This answer is read as a ranking over the roster, not as a decision. Whether "
                "any agent is assigned at all is settled by other questions in this same "
                "request. Name the agent that fits best even when the best fit is a poor one, "
                "and do not let a poor fit change which agent you name."
            ),
        },
        dict(roster),
    )


def stage1_questions(roster: Mapping[str, str]) -> dict[str, dict]:
    """The full stage-1 battery: the live roster Choice plus the roster-independent gates.

    One request. The Choice, both gates, the blast-radius Score, and the diagnostic Noul all
    read one shared state, so they batch (`AGENTS.md` rule 1) rather than fanning out.
    """
    return {"which_wide": which_wide(roster), **GATE_QUESTIONS}


AGENT_ROUTER_STAGE1 = register(
    QuestionSet(
        name="agent_router_stage1",
        version="1.0.0",
        description=(
            "Stage 1 of agent routing: rank the whole spawnable roster against a "
            "task, and gate on whether a specialist is needed and whether the task can be "
            "handed off at all."
        ),
        # Snapshot roster, for `tools/question_critic.py` and the calibration pass. Production
        # callers use `ask_stage1(..., roster=<launcher's spawnable set>)`.
        questions=stage1_questions(load_registry_roster()),
        # Empty on purpose. Every cutoff this set needs is named in REQUIRED_THRESHOLDS with
        # what crossing it means. Inventing a number here would be the exact failure
        # `gates_irreversible` exists to prevent.
        thresholds={},
        gates_irreversible=True,
        validated_on=None,
    )
)


def ask_stage1(
    client: TypeSafeClient,
    task: Any,
    *,
    roster: Mapping[str, str] | None = None,
    allow_unvalidated: bool = False,
) -> Result:
    """Run stage 1 against one task, with the roster resolved at call time.

    Rebuilding the Choice through `dataclasses.replace` keeps `gates_irreversible`, the pinned
    model, and the validation guard intact -- a live roster must not be a way around them.
    """
    state = {"task": task}
    if roster is None:
        return AGENT_ROUTER_STAGE1.ask(
            client, state, allow_unvalidated=allow_unvalidated
        )
    live = dataclasses.replace(AGENT_ROUTER_STAGE1, questions=stage1_questions(roster))
    return live.ask(client, state, allow_unvalidated=allow_unvalidated)
