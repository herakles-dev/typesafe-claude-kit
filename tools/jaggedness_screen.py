#!/usr/bin/env python3
"""jaggedness-screen -- design-time triage against the nine documented jev-1.13 failure modes.

Give it a proposed TypeSafe design (its questions, a description of the state they will
receive, and what the code does with the answers). It reports, per mode, how exposed the
design is, which specific part of the design trips it, and the remedy the upstream doc gives.

    python3 tools/jaggedness_screen.py --file design.json
    python3 tools/jaggedness_screen.py --file design.json --json
    python3 tools/jaggedness_screen.py --schema

Importable:

    from jaggedness_screen import load_design, screen
    report = screen(load_design(Path("design.json")))
    report.top_mode()          # -> 2
    report.finding(2).exposure # -> 0.97

WHAT THIS IS NOT. It ranks; it does not gate. There is no pass/fail cutoff anywhere in this
file, because no cutoff over these numbers has been measured (AGENTS.md rule 7). A design with
every exposure near zero is clean; one with a high exposure on a high-consequence question
needs the remedy applied. Where the line sits between those is `typesafe-calibrator`'s call on
labelled data, not this tool's.

SEVERITY. Exposure and consequence are two different things and the design deliberately keeps
them apart:

  * exposure   -- P(the design trips this mode). A Noul per mode. Absolute, so several can be
                  high at once, which is the truth: a question can count AND compare dates.
  * consequence-- how bad a wrong answer is. A Score over four levels that describe
                  situations, from "a person reads it" to "data is deleted". Ordered levels
                  are exactly what a Score is for; a Noul could not express "reversible".
  * class weight-- a code-side constant per mode, below. The doc is not neutral between modes:
                  counting, date arithmetic and generation are listed as things never to send
                  the model at all, while indirection and irrelevant detail are described as
                  costing accuracy. That ordering is editorial, it is written down here where
                  it can be read and changed, and it is NOT measured.

    severity = class_weight * exposure * consequence

That product is a ranking key, nothing more. It is arithmetic in code over two probabilities
(the pattern every cookbook uses), not a Score interpolated to recover a magnitude -- which
failure mode #2 forbids. All three components are reported separately so a caller who
disagrees with the weighting can re-rank without re-running inference.

MODE 6 AND THE UNTRUSTED-STATE QUESTION. Whether the state is hostile is not a property of the
question wording, so the model cannot screen it from the design text alone. It is required
input: `state_source`, and `downstream_untrusted_handling`. When either is missing the screen
does not quietly pass -- it records mode 6 as UNKNOWN, gives it worst-case exposure so it
sorts to the top, sets `report.complete = False`, and the CLI exits 2. The screen also asks
the model, independently, whether the described state sounds like it carries foreign text, so
an over-optimistic `state_source: internal` declaration is contradicted rather than believed.

FOUR CHECKS THAT ARE NOT UPSTREAM MODES. The nine modes are the upstream list. Red-teaming
real integrations turned up four failure classes none of them covers, and each is reported
here under a string id rather than a number so nobody mistakes it for upstream doctrine. Each
earned its place on a paired probe -- a deliberately-bad design against a twin differing in
exactly one thing -- run twice, 2026-09-20:

  * `attacker_premise` -- "Attacker-settable premise". The question is well posed and the
    model answers it *correctly*; the field it reads is authored by someone outside the
    system, so that outsider sets the answer. Nothing is tricked, so mode 6 does not fire on
    it and no injection test finds it. Measured on a registry design over one state
    description holding both an internally-observed field and a provider-written one:
    the question reading `provider_message` scored **0.98**, its twin reading our own probe
    history **0.08**. Controls: an all-internal design gating a delete scored 0.07 exposure
    against 0.99 consequence; the provider-reading question reduced to a dashboard badge kept
    0.98 exposure but dropped to 0.25 consequence. Exposure and consequence stay orthogonal,
    which is the point -- severity is the product.
    Unlike the other per-question checks, this one picks the question to report by
    `exposure x consequence`, not by exposure alone: the finding is meant to answer "which
    judgment reads an attacker-controlled field, and what does it gate".

  * `correlated_evidence` -- "Correlated evidence". Two questions whose answers travel
    together in ordinary traffic. Accuracy on happy-path data cannot say which one the
    decision rests on, and a single-direction ablation confirms whichever story you already
    believed (MASTERY.md section 9: 66/66 with the status removed *and* 66/66 with the prose
    removed). Ordered correctly across four designs: two fields one event writes together
    **0.91**, HTTP status plus provider prose **0.53**, status plus a tier label our own team
    typed **0.16**, two independent properties of one ticket body **0.11**. The intended pair
    separates 0.53 vs 0.16. Read it as a rank: 0.53 is the honest reading for signals that are
    *usually* rather than *always* joint.

  * `unbanded_cutoff` -- "Unbanded cutoff". A probability turned straight into an action at
    one number, with no middle range held for a person (cookbooks-reliability.md,
    cross-cutting 6 and 7: a hard cutoff turns 0.49-vs-0.51 flicker into opposite treatment,
    and a stable label can hide unstable probabilities). Measured: a single-cutoff delete
    policy **0.98**, the same design with a 0.30/0.85 band routing the middle to a moderator
    **0.03**. Control: a design whose Choice argmax names a queue and compares no probability
    to anything scored **0.08**, so it does not fire on every Choice design.

  * `unmeasured_threshold` -- "Unmeasured threshold". Decided in code, with no API call: does
    `code_policy` name a number used as a cutoff, and is `thresholds_validated_on` declared?
    AGENTS.md rule 7. This is the only place the screen can see the thing that misled us three
    times in one day -- a number resting on a single run. The screen cannot count runs; it can
    demand that the provenance be written down, where "measured once" becomes visible to a
    reader. Deterministic, exact, and not a measurement: exposure is 1.0 or 0.0.

REJECTED, and why, so nobody re-litigates them:

  * A shared-*source* wording of `correlated_evidence` ("do two questions read parts of the
    state written by the same event?") read **0.92** on two genuinely independent questions
    over one ticket body -- higher than the 0.66 it gave the real correlated design. It tracks
    shared provenance, not joint variation, and it inverts on the case that matters.
  * An ablation wording ("could one question be deleted with the same decision on nearly every
    input?") separated 0.245 vs 0.155. Too flat to use.
  * A "picture a hundred inputs" wording of `correlated_evidence` ordered the four designs
    correctly but compressed everything toward zero (0.46 / 0.18 / 0.10 / 0.06). Asking the
    model to imagine a sample is counting-flavoured -- mode 2 in the screen itself.

LIMITATION, measured 2026-09-20. `mode1_literal_reading` does not discriminate. Across every
probe run so far its exposure sits between 0.80 and 0.91 -- a tightly worded Noul with full
contrastive criteria scored 0.80, a deliberately vague one 0.91, and adding proper criteria to
a bare question moved it from 0.80 to 0.83 (the wrong direction). It orders questions, but its
floor is high enough that any absolute cutoff would flag everything. Read mode 1's number as a
relative hint within one design and nothing more. The other eight modes separate cleanly on
paired probes (see tests/): mode 2 0.97/0.25, mode 3 0.96/0.07, mode 4 0.79/0.13,
mode 5 0.99/0.06, mode 7 0.93/0.09, mode 8 0.98/0.06, mode 9 0.95/0.05.

Rewording mode 1 belongs to `typesafe-question-smith`.

KNOWN FALSE POSITIVE. A design whose question text still contains an unsubstituted template
placeholder (`fits::{agent}`, "Does the agent {name} ...") reads as generation and lifts mode
9 -- measured 0.45 on the agent-router design, against 0.05 for its sibling Choices. The
screen judges the text as written. Substitute placeholders before screening, or expect that
hit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from typesafe_client import TypeSafeClient  # noqa: E402
from questions.jaggedness_screen import PER_DESIGN, PER_QUESTION, TAG  # noqa: E402

__all__ = [
    "CHECKS",
    "Design",
    "Finding",
    "ScreenReport",
    "load_design",
    "screen",
    "MODES",
    "EXTRA_CHECKS",
    "REMEDY",
    "CLASS_WEIGHT",
    "UNTRUSTED_SOURCES",
    "SCHEMA",
]

# --- The nine modes, their remedies, and their class weights -------------------------------

MODES: dict[int, str] = {
    1: "Literal reading",
    2: "Math and numbers",
    3: "Date and time comparison",
    4: "Indirection",
    5: "Large state full of irrelevant detail",
    6: "Adversarial content",
    7: "Contradictory instructions and criteria",
    8: "Common-sense structural invariants",
    9: "Generation",
}

#: Checks that are NOT upstream modes. String ids, never numbers, so a report never implies
#: the upstream doc says something it does not. See the module docstring for each one's
#: measured separation.
EXTRA_CHECKS: dict[str, str] = {
    "attacker_premise": "Attacker-settable premise",
    "correlated_evidence": "Correlated evidence",
    "unbanded_cutoff": "Unbanded cutoff",
    "unmeasured_threshold": "Unmeasured threshold",
}

#: Everything the screen reports, numbered modes and named checks alike.
CHECKS: dict[int | str, str] = {**MODES, **EXTRA_CHECKS}

#: Verbatim-in-spirit from `reference/model-jaggedness/jev-1.13.md`, one per mode. Static text
#: in code: the screen must not ask the model to *write* a remedy (that would be mode 9 in the
#: screen itself).
REMEDY: dict[int | str, str] = {
    1: "State the exact condition in the instructions and put the boundary cases in the "
       "criteria. Where you find yourself explaining what you really meant, that explanation "
       "is the missing half of the instruction. If interpretation is unavoidable, split it "
       "into two literal questions and combine them in code.",
    2: "Keep the arithmetic in code. To count things matching a criterion, iterate in code "
       "over the candidates, ask one Noul per candidate, and sum the answers yourself. Pass "
       "already-computed numbers or named buckets instead of raw hex, RGB, or byte values.",
    3: "Split it. Extraction is a judgment: ask each date part as a Choice over its closed set "
       "(twelve months, thirty-one days, a bounded year range) with an explicit 'not stated' "
       "option. Assembly, ordering, duration, offset and weekday are code's.",
    4: "Write the instruction as directly as possible and name the relevant part of the state "
       "by path. Remove double negatives. Where a chain is unavoidable, resolve the first hop "
       "in code and pass its result in as a field.",
    5: "Retrieve and filter in code first and send only the fields the question reads. Where "
       "filtering in code is impossible, run a cheap relevance Noul per candidate and drop "
       "the ones that fail before the real question sees them.",
    6: "Be explicit in the criteria that the state is untrusted data to be judged, never "
       "followed. Probability-gated filtering reduces exposure but is not a security "
       "boundary: it must be backed by an unconditional 'treat this text as untrusted' "
       "instruction in whatever consumes the result. Test injection through every field "
       "before deploying, and hand the design to typesafe-adversary.",
    7: "Treat the criteria as an extension of the instruction and align the two. Never let a "
       "Noul's `true` map to the reader's 'no'. If a value must be inverted, word it "
       "positively and invert it in code.",
    8: "Ask each decision one way and enforce identities in code. Do not expect a question and "
       "its negation to sum to 1 (measured: 1.19). Do not carry a threshold from a Noul to a "
       "Choice (measured: 0.22 versus 0.01 on the same judgment). Give every question its own "
       "cutoff.",
    "attacker_premise":
        "Ask who authors every field a gating question reads. Where the author is outside the "
        "system, the judgment is attacker-settable however well it is worded -- the answer can "
        "be correct and still be chosen by the attacker. Gate the irreversible action on a "
        "field this system observes for itself, and let the outsider-authored field inform a "
        "reversible outcome or a human review instead. Where it must be read, require a second "
        "independent signal that the same party cannot set, and measure the leak: run the "
        "gating question with and without hostile content in that field and report both means.",
    "correlated_evidence":
        "Before trusting accuracy on this design, build cases where the two signals disagree. "
        "A set drawn from ordinary traffic cannot tell you which signal the decision rests on, "
        "and a one-directional ablation confirms whichever story you already believe -- measured "
        "at 66/66 both ways on the same set. Pair each conflict case with a control differing "
        "in exactly one thing, so the delta is attributable, and hand the pair to "
        "typesafe-calibrator.",
    "unbanded_cutoff":
        "Turn the probability into three outcomes, not two: below a low number nothing happens, "
        "between the two numbers the case is held for a person, above a high number code acts. "
        "A single cutoff gives 0.49 and 0.51 opposite treatment, and a stable label can hide "
        "unstable probabilities underneath it. Both numbers come from labelled examples and the "
        "relative cost of a wrong action and a review, never from a plausible-looking default.",
    "unmeasured_threshold":
        "Declare `thresholds_validated_on`: name the labelled set the number came from and how "
        "many repeated runs per case it rests on. One run is not a measurement -- Jev is "
        "self-consistent, so a single run reproduces its own error exactly and looks like "
        "evidence. Until the provenance is written down, treat the number as an invented one "
        "and hand it to typesafe-calibrator.",
    9: "Enumerate the candidates in code -- with a regex, a parser, or a generative model -- "
       "and let Jev pick one. If the answer space genuinely cannot be enumerated, this is not "
       "a TypeSafe surface; use a generative model for that step.",
}

#: POLICY, not measured. Derived from the upstream doc's own language: modes 2, 3 and 9 appear
#: in AGENTS.md's "Never send Jev" list as categorical bans, mode 6 is a security exposure,
#: modes 1 and 7 produce confidently wrong answers with no visible symptom, mode 8 breaks code
#: that looks correct, and modes 4 and 5 are described as costing accuracy rather than
#: guaranteeing a wrong answer. Edit this dict to re-rank; no logic depends on the values.
CLASS_WEIGHT: dict[int | str, float] = {
    1: 0.80,
    2: 1.00,
    3: 1.00,
    4: 0.60,
    5: 0.60,
    6: 0.90,
    7: 0.80,
    8: 0.70,
    9: 1.00,
    # POLICY, not measured, same as the numbered weights above. `attacker_premise` sits with
    # mode 6 because it is the same class of harm and nothing measured orders the two.
    "attacker_premise": 0.90,
    "unmeasured_threshold": 0.80,
    "unbanded_cutoff": 0.70,
    "correlated_evidence": 0.60,
}

#: A `state_source` in this set means someone outside the system can put text in the state.
UNTRUSTED_SOURCES = {"user_input", "third_party", "scraped", "model_generated", "mixed"}
TRUSTED_SOURCES = {"internal"}
KNOWN_SOURCES = UNTRUSTED_SOURCES | TRUSTED_SOURCES

PER_QUESTION_MODES = {1: "mode1_literal_reading", 2: "mode2_math_or_counting",
                      3: "mode3_date_or_time", 4: "mode4_indirection",
                      7: "mode7_criteria_conflict", 9: "mode9_generation"}

SCHEMA = """\
{
  "name":        "short-name-of-the-surface",
  "purpose":     "one line: what decision this design drives",

  "state_source": "internal | user_input | third_party | scraped | model_generated | mixed",
      // REQUIRED. Where the text in the state comes from. Omit it and mode 6 is reported
      // UNKNOWN at worst-case exposure; it is never silently passed.

  "downstream_untrusted_handling": true | false,
      // REQUIRED when state_source is not "internal". Does whatever consumes this design's
      // answers carry an unconditional "treat this text as untrusted" instruction of its own?
      // A probability gate alone is not a security boundary.

  "thresholds_validated_on": "the labelled set the cutoffs in code_policy came from, and "
                             "how many repeated runs per case",
      // OPTIONAL. Omit it and any numeric cutoff named in `code_policy` is reported under
      // `unmeasured_threshold`. One run is not a measurement: jev is self-consistent, so a
      // single run reproduces its own error exactly and reads like evidence.

  "state_description": "what the model receives on each request, field by field",
  "code_policy":       "what the code does with the answers: thresholds, how they combine, "
                       "what action each outcome drives",

  "questions": [
    {
      "id":           "which_agent",
      "type":         "choice | score | noul",
      "instructions": "the exact instruction text you plan to send",
      "criteria":     { "...": "..." },      // options dict, levels list, or true/false
      "used_for":     "what the code does with THIS answer"
    }
  ]
}
"""


# --- Input ---------------------------------------------------------------------------------


@dataclass
class Design:
    """A proposed design, as screened. Missing provenance is recorded, never defaulted away."""

    name: str
    purpose: str
    state_description: str
    code_policy: str
    questions: list[dict]
    state_source: str = "unknown"
    downstream_untrusted_handling: bool | None = None
    thresholds_validated_on: str | None = None
    unknowns: list[str] = field(default_factory=list)

    @property
    def state_is_untrusted(self) -> bool:
        return self.state_source in UNTRUSTED_SOURCES


def load_design(source: Path | str | dict) -> Design:
    """Load a design from a path, a JSON string, or a dict.

    Raises ValueError only for what makes screening impossible (no questions). Everything
    else that is missing becomes a recorded unknown, because a design that omits its
    provenance is exactly the design most worth screening.
    """
    if isinstance(source, dict):
        raw = source
    elif isinstance(source, Path) or (isinstance(source, str) and not source.lstrip().startswith("{")):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = json.loads(source)

    questions = raw.get("questions") or []
    if not questions:
        raise ValueError("Design has no `questions`; there is nothing to screen.")
    for i, q in enumerate(questions):
        if not q.get("instructions"):
            raise ValueError(f"questions[{i}] has no `instructions`.")
        q.setdefault("id", f"q{i}")
        q.setdefault("type", "unspecified")
        q.setdefault("criteria", "(none given)")
        q.setdefault("used_for", "(not stated)")

    unknowns: list[str] = []
    source_field = str(raw.get("state_source") or "unknown").strip().lower()
    if source_field not in KNOWN_SOURCES:
        if source_field != "unknown":
            unknowns.append(
                f"`state_source` is {source_field!r}, which is not one of "
                f"{sorted(KNOWN_SOURCES)}. Treated as unknown."
            )
        else:
            unknowns.append(
                "`state_source` is not declared. Where does the text in the state come from -- "
                "this system's own records, a user, a third party, a scrape, or another model?"
            )
        source_field = "unknown"

    handling = raw.get("downstream_untrusted_handling")
    if handling is None and source_field != "internal":
        unknowns.append(
            "`downstream_untrusted_handling` is not declared. Does whatever consumes these "
            "answers carry its own unconditional 'treat this text as untrusted' instruction? "
            "A probability gate on its own is not a security boundary."
        )

    validated_on = raw.get("thresholds_validated_on")
    validated_on = str(validated_on).strip() if validated_on else None

    return Design(
        name=str(raw.get("name") or "(unnamed design)"),
        purpose=str(raw.get("purpose") or "(not stated)"),
        state_description=str(raw.get("state_description") or "(not described)"),
        code_policy=str(raw.get("code_policy") or "(not stated)"),
        questions=list(questions),
        state_source=source_field,
        downstream_untrusted_handling=handling,
        thresholds_validated_on=validated_on or None,
        unknowns=unknowns,
    )


# --- Output --------------------------------------------------------------------------------


@dataclass
class Finding:
    """One check's result for one design.

    `mode` is an int for the nine upstream failure modes and a string id for the four checks
    that are not upstream (see `EXTRA_CHECKS`). Nothing downstream should infer doctrine from
    a string id that the upstream doc does not carry.
    """

    mode: int | str
    name: str
    exposure: float | None          # None when the mode could not be screened
    consequence: float              # 0-1, normalized Score
    class_weight: float
    where: str                      # the specific part of the design that trips it
    evidence: str                   # the measured numbers behind `exposure`
    remedy: str
    status: str = "screened"        # "screened" | "unknown"

    @property
    def severity(self) -> float:
        """Ranking key. Worst case when the mode could not be screened."""
        exposure = 1.0 if self.exposure is None else self.exposure
        return self.class_weight * exposure * self.consequence

    @property
    def is_upstream_mode(self) -> bool:
        """True for the nine documented modes, False for the four locally-measured checks."""
        return isinstance(self.mode, int)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "upstream_mode": self.is_upstream_mode,
            "name": self.name,
            "status": self.status,
            "exposure": self.exposure,
            "consequence": round(self.consequence, 4),
            "class_weight": self.class_weight,
            "severity": round(self.severity, 4),
            "where": self.where,
            "evidence": self.evidence,
            "remedy": self.remedy,
        }


@dataclass
class ScreenReport:
    design: Design
    findings: list[Finding]
    requests: int
    input_tokens: int
    cost_usd: float
    latency_ms: int
    model: str

    @property
    def complete(self) -> bool:
        """False when any mode could not be screened -- provenance is the usual reason."""
        return not self.design.unknowns and all(f.status == "screened" for f in self.findings)

    def ranked(self) -> list[Finding]:
        # Ties break numbered modes first, then named checks alphabetically. int and str are
        # not orderable against each other, so the key separates them before comparing.
        return sorted(self.findings, key=lambda f: (
            -f.severity, 0 if isinstance(f.mode, int) else 1,
            f.mode if isinstance(f.mode, int) else 0,
            "" if isinstance(f.mode, int) else f.mode,
        ))

    def finding(self, mode: int | str) -> Finding:
        for f in self.findings:
            if f.mode == mode:
                return f
        known = [f.mode for f in self.findings]
        raise KeyError(f"No finding for {mode!r}. Known: {known}")

    def top_mode(self) -> int | str:
        return self.ranked()[0].mode

    def exposure(self, mode: int | str) -> float | None:
        return self.finding(mode).exposure

    def upstream_findings(self) -> list[Finding]:
        """The nine documented modes only, worst first."""
        return [f for f in self.ranked() if f.is_upstream_mode]

    def to_dict(self) -> dict:
        return {
            "design": self.design.name,
            "model": self.model,
            "complete": self.complete,
            "unknowns": self.design.unknowns,
            "state_source": self.design.state_source,
            "downstream_untrusted_handling": self.design.downstream_untrusted_handling,
            "thresholds_validated_on": self.design.thresholds_validated_on,
            "findings": [f.to_dict() for f in self.ranked()],
            "cost": {
                "requests": self.requests,
                "input_tokens": self.input_tokens,
                "usd": round(self.cost_usd, 8),
                "latency_ms": self.latency_ms,
            },
        }


# --- The screen ----------------------------------------------------------------------------


def _question_state(design: Design, q: dict) -> dict:
    """State for one per-question request: exactly one question, plus the context it needs.

    One question per request, not all of them with an index. Asking the model about
    `questions[3].criteria` would make the screen itself multi-hop -- failure mode #4.
    """
    return {
        "design": {
            "name": design.name,
            "purpose": design.purpose,
            "state_the_model_will_receive": design.state_description,
        },
        "question": {
            "id": q["id"],
            "type": q["type"],
            "instructions": q["instructions"],
            "criteria": q["criteria"],
            "used_for": q["used_for"],
        },
    }


def _design_state(design: Design) -> dict:
    """State for the one design-level request.

    Question *criteria* are included because mode 6 asks whether any of them declares the
    state untrusted, and mode 8 asks whether two questions duplicate a judgment.
    """
    return {
        "design": {"name": design.name, "purpose": design.purpose},
        "state_description": design.state_description,
        "code_policy": design.code_policy,
        "questions_asked": [
            {"id": q["id"], "type": q["type"], "instructions": q["instructions"],
             "criteria": q["criteria"]}
            for q in design.questions
        ],
    }


def screen(design: Design, client: TypeSafeClient | None = None) -> ScreenReport:
    """Screen a design. One request for the design, one per question. Sequential by design:
    a failed request should be attributable to the question that caused it."""
    client = client or TypeSafeClient()
    requests_made = tokens = latency = 0
    cost = 0.0
    model = ""

    def run(qset, state) -> Any:
        nonlocal requests_made, tokens, latency, cost, model
        result = client.ask(state=state, questions=qset.questions, model=qset.model, tag=TAG)
        requests_made += 1
        tokens += result.input_tokens
        cost += result.cost_usd
        latency += result.latency_ms
        model = result.model or model
        return result

    # --- per-question modes: 1, 2, 3, 4, 7, 9 ----------------------------------------------
    per_question: dict[int, list[tuple[str, float]]] = {m: [] for m in PER_QUESTION_MODES}
    question_consequence: dict[str, float] = {}

    attacker: list[tuple[str, float, str]] = []

    for q in design.questions:
        result = run(PER_QUESTION, _question_state(design, q))
        question_consequence[q["id"]] = result.normalized_score("consequence")
        for mode, qid in PER_QUESTION_MODES.items():
            per_question[mode].append((q["id"], result.noul(qid)))
        attacker.append((q["id"], result.noul("attacker_settable_premise"),
                         str(q.get("used_for") or "(not stated)")))

    findings: list[Finding] = []
    for mode, hits in per_question.items():
        worst_id, worst = max(hits, key=lambda pair: pair[1])
        findings.append(Finding(
            mode=mode,
            name=MODES[mode],
            exposure=worst,
            consequence=question_consequence[worst_id],
            class_weight=CLASS_WEIGHT[mode],
            where=f"question {worst_id!r}",
            evidence="; ".join(f"{qid}={value:.2f}" for qid, value in hits),
            remedy=REMEDY[mode],
        ))

    # --- design-level modes: 5, 6, 8 -------------------------------------------------------
    d = run(PER_DESIGN, _design_state(design))
    design_consequence = d.normalized_score("consequence")

    irrelevant = d.noul("mode5_irrelevant_state")
    large = d.noul("mode5_state_is_large")
    findings.append(Finding(
        mode=5,
        name=MODES[5],
        # Irrelevant detail is the failure; size multiplies how much of it there is.
        exposure=irrelevant,
        consequence=design_consequence,
        class_weight=CLASS_WEIGHT[5],
        where="the state description",
        evidence=f"carries_unneeded_material={irrelevant:.2f}; state_is_large={large:.2f}",
        remedy=REMEDY[5],
    ))

    foreign = d.noul("mode6_carries_foreign_text")
    declared = d.noul("mode6_criteria_declare_untrusted")
    findings.append(_mode6_finding(design, foreign, declared, design_consequence))

    invariant = d.noul("mode8_invariant_reliance")
    duplicate = d.noul("mode8_duplicate_judgment")
    findings.append(Finding(
        mode=8,
        name=MODES[8],
        exposure=max(invariant, duplicate),
        consequence=design_consequence,
        class_weight=CLASS_WEIGHT[8],
        where="code_policy" if invariant >= duplicate else "the question set",
        evidence=f"policy_relies_on_identity={invariant:.2f}; duplicate_judgment={duplicate:.2f}",
        remedy=REMEDY[8],
    ))

    findings.append(_attacker_premise_finding(attacker, question_consequence))

    correlated = d.noul("correlated_evidence")
    findings.append(Finding(
        mode="correlated_evidence", name=EXTRA_CHECKS["correlated_evidence"],
        exposure=correlated, consequence=design_consequence,
        class_weight=CLASS_WEIGHT["correlated_evidence"],
        where="the question set, read against the traffic it will see",
        evidence=f"answers_travel_together={correlated:.2f} (0.91 measured on two fields one "
                 f"event writes; 0.11 on two independent properties of one body)",
        remedy=REMEDY["correlated_evidence"],
    ))

    unbanded = d.noul("unbanded_cutoff")
    findings.append(Finding(
        mode="unbanded_cutoff", name=EXTRA_CHECKS["unbanded_cutoff"],
        exposure=unbanded, consequence=design_consequence,
        class_weight=CLASS_WEIGHT["unbanded_cutoff"],
        where="code_policy",
        evidence=f"single_cutoff_no_review_band={unbanded:.2f}",
        remedy=REMEDY["unbanded_cutoff"],
    ))

    findings.append(_unmeasured_threshold_finding(design, design_consequence))

    return ScreenReport(
        design=design, findings=findings, requests=requests_made,
        input_tokens=tokens, cost_usd=cost, latency_ms=latency,
        model=model or PER_DESIGN.model,
    )


def _attacker_premise_finding(attacker: list[tuple[str, float, str]],
                              consequence: dict[str, float]) -> Finding:
    """Which judgment reads a field an outsider authors, and what does that judgment gate?

    Selection differs from the other per-question checks on purpose. They report the question
    with the worst exposure; this one reports the question with the worst
    `exposure x consequence`, because a design's real attacker-settable judgment is the one
    that both reads foreign text *and* drives something. A question reading the same field to
    paint a dashboard badge measured 0.98 exposure against 0.25 consequence -- correctly
    exposed, correctly harmless.
    """
    worst_id, worst, used_for = max(
        attacker, key=lambda t: t[1] * consequence.get(t[0], 1.0))
    evidence = "; ".join(f"{qid}={value:.2f}" for qid, value, _ in attacker)
    return Finding(
        mode="attacker_premise", name=EXTRA_CHECKS["attacker_premise"],
        exposure=worst, consequence=consequence[worst_id],
        class_weight=CLASS_WEIGHT["attacker_premise"],
        where=f"question {worst_id!r}, which gates: {used_for}",
        evidence=evidence + " -- ranked by exposure x consequence, not exposure alone",
        remedy=REMEDY["attacker_premise"],
    )


#: A cutoff a policy compares a probability against. Deliberately narrow: a bare decimal
#: between 0 and 1, or a percentage. `0.042` in a cost note would match, which is why the
#: finding is advisory like every other one and says what it matched.
_CUTOFF_PATTERN = re.compile(r"(?<![\w.])(?:0?\.\d+|[1-9]?\d(?:\.\d+)?\s*%)(?![\w.])")


def _unmeasured_threshold_finding(design: Design, consequence: float) -> Finding:
    """Decided in code. No API call: this is `re` and a dict lookup, not a judgment.

    Exposure is 1.0 or 0.0 and is not a measurement -- it is the answer to a question with an
    exact answer. The screen cannot see how many runs a number rests on; it can see whether
    anyone wrote down where the number came from, which is where "measured once" becomes
    visible to the next reader.
    """
    matches = sorted(set(_CUTOFF_PATTERN.findall(design.code_policy)))
    declared = design.thresholds_validated_on

    if not matches:
        return Finding(
            mode="unmeasured_threshold", name=EXTRA_CHECKS["unmeasured_threshold"],
            exposure=0.0, consequence=consequence,
            class_weight=CLASS_WEIGHT["unmeasured_threshold"],
            where="code_policy",
            evidence="no numeric cutoff found in code_policy (deterministic, no API call)",
            remedy=REMEDY["unmeasured_threshold"],
        )
    if declared:
        return Finding(
            mode="unmeasured_threshold", name=EXTRA_CHECKS["unmeasured_threshold"],
            exposure=0.0, consequence=consequence,
            class_weight=CLASS_WEIGHT["unmeasured_threshold"],
            where="code_policy",
            evidence=f"cutoff(s) {matches} declared validated on: {declared} "
                     "(deterministic, no API call)",
            remedy=REMEDY["unmeasured_threshold"],
        )
    return Finding(
        mode="unmeasured_threshold", name=EXTRA_CHECKS["unmeasured_threshold"],
        exposure=1.0, consequence=consequence,
        class_weight=CLASS_WEIGHT["unmeasured_threshold"],
        where="code_policy",
        evidence=f"cutoff(s) {matches} named with no `thresholds_validated_on` "
                 "(deterministic, no API call)",
        remedy=REMEDY["unmeasured_threshold"],
    )


def _mode6_finding(design: Design, foreign: float, declared: float,
                   consequence: float) -> Finding:
    """Mode 6 combines a declared fact with two measured ones. Code owns the combination.

    `declared` is worded positively (high = the criteria say the state is untrusted) because a
    Noul whose `true` reads as "no" performs worse -- failure mode #7. It is inverted here, on
    the code side, exactly as the agent-router design inverts `generalist_suffices`.
    """
    unhandled = 1.0 - declared
    evidence = (f"state_source={design.state_source}; "
                f"downstream_untrusted_handling={design.downstream_untrusted_handling}; "
                f"model_reads_state_as_foreign={foreign:.2f}; "
                f"criteria_declare_untrusted={declared:.2f}")

    if design.state_source == "unknown":
        return Finding(
            mode=6, name=MODES[6], exposure=None, consequence=consequence,
            class_weight=CLASS_WEIGHT[6], status="unknown",
            where="`state_source`, which the design does not declare",
            evidence=evidence + " -- provenance undeclared, so exposure is unscreenable",
            remedy=("Declare `state_source`. Until then assume untrusted. " + REMEDY[6]),
        )

    if design.state_source == "internal" and foreign > declared:
        # The design claims internal, the model reads the description as carrying foreign
        # text. That contradiction is worth surfacing whatever the numbers are.
        return Finding(
            mode=6, name=MODES[6], exposure=foreign, consequence=consequence,
            class_weight=CLASS_WEIGHT[6], status="screened",
            where="`state_source: internal`, contradicted by the state description",
            evidence=evidence + " -- declared internal but the described state reads as "
                                "carrying text from outside the system",
            remedy=REMEDY[6],
        )

    if design.state_source == "internal":
        return Finding(
            mode=6, name=MODES[6], exposure=foreign * unhandled, consequence=consequence,
            class_weight=CLASS_WEIGHT[6], status="screened",
            where="the state (declared internal)",
            evidence=evidence,
            remedy=REMEDY[6],
        )

    where = "the state, which carries text from outside the system"
    extra = ""
    if design.downstream_untrusted_handling is not True:
        extra = (" -- no downstream untrusted-text handling is declared, so the probability "
                 "gate is the only thing standing between an injected instruction and the "
                 "action. That is not a security boundary.")
        where += ", with no declared downstream untrusted-text handling"
    return Finding(
        mode=6, name=MODES[6], exposure=max(foreign * unhandled, unhandled if extra else 0.0),
        consequence=consequence, class_weight=CLASS_WEIGHT[6], status="screened",
        where=where, evidence=evidence + extra, remedy=REMEDY[6],
    )


# --- CLI -----------------------------------------------------------------------------------


def _bar(value: float | None, width: int = 10) -> str:
    if value is None:
        return "?" * width
    filled = int(round(value * width))
    return "#" * filled + "." * (width - filled)


def _render(report: ScreenReport) -> str:
    d = report.design
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"  JAGGEDNESS SCREEN  --  {d.name}")
    out.append(f"  {d.purpose}")
    out.append(f"  model {report.model} | {len(d.questions)} question(s) | "
               f"state_source={d.state_source}")
    out.append("=" * 78)
    out.append("")
    out.append("  Advisory. Ranks exposure; sets no cutoff. severity = weight x exposure x "
               "consequence.")
    out.append("")
    out.append(f"  {'#':<4} {'check':<36} {'expo':>5} {'cons':>5} {'sev':>5}  exposure")
    out.append(f"  {'-' * 4} {'-' * 36} {'-' * 5} {'-' * 5} {'-' * 5}  {'-' * 10}")
    for f in report.ranked():
        expo = "  ?  " if f.exposure is None else f"{f.exposure:5.2f}"
        tag = str(f.mode) if f.is_upstream_mode else "*"
        out.append(f"  {tag:<4} {f.name[:36]:<36} {expo} {f.consequence:5.2f} "
                   f"{f.severity:5.2f}  {_bar(f.exposure)}")
    out.append("")
    out.append("  Rows numbered 1-9 are the upstream failure modes. Rows marked * are checks "
               "measured here,")
    out.append("  not upstream doctrine -- see the module docstring for each one's paired "
               "separation.")
    out.append("")
    out.append("-" * 78)
    out.append("  FINDINGS, worst first")
    out.append("-" * 78)
    for f in report.ranked():
        head = f"[{f.mode}] {f.name}" if f.is_upstream_mode else f"[*] {f.name}"
        if f.status == "unknown":
            head += "  -- NOT SCREENABLE FROM THE DESIGN"
        out.append("")
        out.append(f"  {head}")
        out.append(f"    where    {f.where}")
        out.append(f"    measured {f.evidence}")
        out.append(f"    instead  {_wrap(f.remedy, 68, 13)}")

    if d.unknowns:
        out.append("")
        out.append("-" * 78)
        out.append("  UNANSWERED -- the screen is incomplete until these are declared")
        out.append("-" * 78)
        for u in d.unknowns:
            out.append(f"    * {_wrap(u, 70, 6)}")

    out.append("")
    out.append("-" * 78)
    out.append(f"  {report.requests} requests | {report.input_tokens} input tokens | "
               f"${report.cost_usd:.6f} | {report.latency_ms} ms total")
    out.append(f"  complete: {report.complete}")
    out.append("-" * 78)
    return "\n".join(out)


def _wrap(text: str, width: int, indent: int) -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return ("\n" + " " * indent).join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jaggedness-screen",
        description=("Screen a proposed TypeSafe design against the nine jev-1.13 failure "
                     "modes, plus four checks measured here."),
    )
    parser.add_argument("--file", help="path to the design JSON")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--schema", action="store_true", help="print the input schema and exit")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.schema:
        print(SCHEMA)
        return 0
    if not args.file:
        parser.error("--file is required (or --schema)")

    report = screen(load_design(Path(args.file)))
    print(json.dumps(report.to_dict(), indent=2) if args.json else _render(report))
    return 0 if report.complete else 2


if __name__ == "__main__":
    sys.exit(main())
