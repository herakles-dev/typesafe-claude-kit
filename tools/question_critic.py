"""question-critic -- judge a drafted TypeSafe question against the documented craft rules.

Dogfooding: Jev reviews Jev questions. The state is the draft itself, and each rule from
`knowledge/MASTERY.md` §3/§5/§6 becomes one atomic judgment whose answer this module composes
into a ranked list of fixes.

Two deliberate splits between code and judgment:

- **Code decides which questions to send.** `draft["type"]` is known before the call, so
  asking a Score-only check about a Noul draft is not a speculative question -- it is a
  question whose branch code already resolved. AGENTS.md rule 1's "ask everything speculative"
  covers answers you *might* need; it does not license padding state with checks that cannot
  apply. Batching is preserved: one request per draft, all applicable checks in it.
- **Code owns everything mechanical.** Level counts, option counts, bare-number levels,
  backticked state paths and escape-option names are regex and `len()`. Sending them to a
  model would be paying for arithmetic Jev is documented to be bad at.

This tool is advisory -- it blocks nothing -- so `CRITIC_SET` sets `gates_irreversible=False`
and carries no thresholds. The bands used for display are provisional and labelled as such.

CLI:

    python3 tools/question_critic.py --file draft.json
    python3 tools/question_critic.py --file draft.json --state sample_state.json
    python3 tools/question_critic.py --file questions.json --json
    cat draft.json | python3 tools/question_critic.py

Import:

    from question_critic import critique, critique_many
    report = critique({"type": "score", "instructions": "...", "criteria": [...]},
                      sample_state={"ticket": {"body": "..."}})
    report.findings[0].fix

LIMITATIONS -- read before trusting a number
--------------------------------------------
Measured on this tool's own checks. Both are handed to `typesafe-calibrator`.

1. **Supply a `sample_state` when the draft uses backticked paths.** Without one, the critic
   cannot tell a path that resolves from one that dangles, so `dangling_state_path` is not
   asked at all and appears under NOT CHECKED. An unasked check is not a passing one.
   An earlier version had no such state and folded path resolution into `unstated_context`,
   which then fired at 0.40-0.81 on every path-using draft *including known-good controls*,
   bimodal at levels 0 and 3 at confidence 0.00. The bimodality was the tell: one question
   was carrying two judgments. They are now separate questions.

2. **`unstated_context` has a low ceiling on confidence for healthy drafts.** Levels 0 and 1
   genuinely blur -- "fully self-contained" versus "one loose term" is a fine boundary -- so
   a good draft lands around 0.13-0.31 with confidence often below 0.20. Read it as a rank
   against other drafts, not as an absolute reading. Likewise a well-scoped instruction floors
   at roughly 0.31 on `hidden_judgments`, because a scoping clause reads as a qualifier; that
   is the healthy baseline, not a defect.

3. **`levels_unordered` over-fired before it was reframed**, and one residual remains: it
   scores ~0.71 on this tool's own `option_descriptions` levels, which are monotonic. Its
   root cause was different from (1) -- positional scanning across level pairs, not a missing
   state -- and reframing it from pairwise comparison to whole-list recognition took its range
   from a useless 0.10-0.41 to 0.05 (ordered) versus 0.95 (scrambled).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from questions import QuestionSet, register  # noqa: E402
from typesafe_client import TypeSafeClient, choice, noul, score  # noqa: E402

__all__ = [
    "CRITIC_SET",
    "CHECKS",
    "Finding",
    "Report",
    "Structural",
    "critique",
    "critique_many",
    "questions_for",
]

TAG = "question-critic"

PRIMITIVES = ("choice", "score", "noul")

#: Display banding only. These are NOT validated thresholds: `CRITIC_SET.thresholds` is
#: deliberately empty and `validated_on` is None. Nothing in this module gates on them, and
#: `typesafe-calibrator` must measure them against hand-labelled drafts before anything does.
PROVISIONAL_BANDS = ((0.70, "high"), (0.40, "medium"), (0.0, "low"))


# --- The questions -------------------------------------------------------------------------
#
# Orientation rule: every Score here runs low -> high in *severity*, so level 0 is always the
# healthy draft and the top level is always the broken one. Levels are judged independently,
# so the model is indifferent to this; it exists so `normalized_score` and `noul` can be read
# on one scale when ranking findings.

UNIVERSAL_QUESTIONS: dict[str, dict] = {
    "hidden_judgments": score(
        {
            # Phrased as recognition, not counting. An earlier draft asked "how many distinct
            # judgments...", and the critic's own `needs_forbidden_reasoning` check scored it
            # 0.78 -- correctly, since counting is on the documented never-send list. The
            # levels already carry the distinctions, so the stem does not need a number.
            "question": (
                "How far does `draft.instructions` bundle separate judgments behind one answer?"
            ),
            "context": (
                "`draft` is a question that will be sent to a model which returns exactly one "
                "typed answer. A question that bundles several judgments returns one number "
                "that cannot be traced back to any of them."
            ),
            "focus": (
                "Judge the wording of `draft.instructions` only. Bundling that appears in "
                "`draft.criteria` is judged by a separate question and does not count here. A "
                "judgment is separate when a reader would have to settle it on its own before "
                "answering; a condition that merely narrows what to look at belongs to one "
                "judgment rather than forming another."
            ),
        },
        [
            "Asks for one judgment only. No input could satisfy part of the question while "
            "failing another part of it.",
            "Asks for one judgment, but attaches a qualifier or condition that a reader could "
            "reasonably settle on its own.",
            "Asks for two judgments at once. An input could clearly satisfy one and clearly "
            "fail the other.",
            "Asks for several judgments at once, or asks for an overall verdict assembled from "
            "sub-judgments the question never names.",
        ],
    ),
    "best_primitive": choice(
        {
            "question": "Which answer shape does `draft.instructions` actually call for?",
            "focus": (
                "Judge from what the question asks and from `purpose`, which says what the "
                "calling code does with the answer. Do not be led by `draft.type`; it is the "
                "shape the author declared and it may be the mistake."
            ),
        },
        {
            "choice": {
                "what": "One item from a fixed set of named alternatives that have no order "
                        "between them.",
                "not_for": "A single yes/no condition, or a position on a graded scale.",
                "examples": [
                    "Which team should handle this ticket?",
                    "Which of these candidate values appears in the document?",
                ],
            },
            "score": {
                "what": "A position on an ordered scale whose steps can each be described as a "
                        "distinct situation.",
                "not_for": "An unordered set of alternatives, or a single yes/no condition.",
                "examples": [
                    "How severe is the reported issue?",
                    "How complete is this record?",
                ],
            },
            "noul": {
                "what": "Whether one stated condition holds, where the probability that it "
                        "holds is itself the signal the code wants.",
                "not_for": "Choosing among alternatives, or measuring how much of something "
                           "there is.",
                "examples": [
                    "Does the customer ask for a refund?",
                    "Does the sending domain differ from the organization named in the message?",
                ],
            },
            "not_a_judgment": {
                "what": "None of the three shapes can carry the answer: it needs free text, a "
                        "quantity that is not one of a few named bands, a list, or a "
                        "calculation.",
                "not_for": "A question one of the three shapes could answer, even when the "
                           "draft declared a different shape from the one that fits.",
                "examples": [
                    "Summarize the complaint in one sentence.",
                    "How many days remain until the invoice is due?",
                ],
            },
        },
    ),
    "declared_primitive_wrong": noul(
        {
            "question": (
                "Is the shape named in `draft.type` the wrong shape for the answer that "
                "`draft.instructions` asks for?"
            ),
            "shapes": {
                "choice": "returns which one of the listed options wins, measured only against "
                          "the other options",
                "score": "returns a position along the ordered levels listed in `draft.criteria`",
                "noul": "returns one probability that a single stated condition holds, in "
                        "absolute terms",
            },
            "focus": (
                "Answer true only when the declared shape cannot carry the answer at all. When "
                "the declared shape does carry it, answer false, even where a different shape "
                "would carry it too."
            ),
            "examples_of_true": [
                "A question asking which of five teams should handle a ticket, declared as a "
                "noul -- one probability cannot name a team.",
                "A question asking how strongly a customer feels, declared as a noul -- the "
                "caller wants an intensity, and a probability that a condition holds is not one.",
                "A question asking whether a condition holds at all, declared as a choice -- "
                "the options only compete against each other and can never all be wrong.",
            ],
            "examples_of_false": [
                "A question asking whether the sending domain differs from the organization "
                "named in the message, declared as a noul.",
                "A question asking how severe a problem is, declared as a score with described "
                "levels.",
                "A question asking which team should handle a ticket, declared as a choice.",
            ],
        },
        {
            "true": "The declared shape cannot carry the answer the question asks for.",
            "false": "The declared shape carries the answer, even if a different shape would "
                     "also work.",
        },
    ),
    "unstated_context": score(
        {
            # Scoped to rules the draft must supply in words. An earlier version also let the
            # model weigh whether backticked paths resolved, which bundled two judgments: it
            # went bimodal at levels 0 and 3 at confidence 0.00 on every draft carrying a
            # path, because a path points outside the critic's own state. Path resolution is
            # now `dangling_state_path`, which is only asked when a sample state exists.
            "question": (
                "How much of what `draft.instructions` needs in order to be answered does the "
                "draft leave undefined in its own words?"
            ),
            "context": (
                "The model that answers this draft sees `draft.instructions`, "
                "`draft.criteria`, and the application state. A backticked path such as "
                "`ticket.body` names a place in that application state; treat every such path "
                "as resolving to the value it names. Whether a path really resolves is settled "
                "by a different question and is not in scope here."
            ),
            "focus": (
                "Judge only the rules the draft has to put into words: cutoffs, time windows, "
                "category definitions, and scoping rules. A term that `draft.instructions` or "
                "`draft.criteria` defines counts as defined, and a value the draft reads out of "
                "the state counts as available."
            ),
        },
        [
            "Every rule needed to answer is written into `draft.instructions` or "
            "`draft.criteria`.",
            "The rules are written down, but one term is loose enough that two careful readers "
            "could put the boundary in different places.",
            "A cutoff, a time window, or a category that the answer turns on is named but "
            "never defined, so the reader has to invent it.",
            "The draft defers to something it does not contain -- the name it is filed under, "
            "a policy kept in the calling code, or a document held elsewhere -- and without "
            "that thing the question cannot be answered at all.",
        ],
    ),
    "dangling_state_path": noul(
        {
            "question": (
                "Does `draft.instructions` name a backticked path that `sample_state` does not "
                "contain?"
            ),
            "context": (
                "`sample_state` is an example of the application state this draft will be run "
                "against. A backticked path such as `ticket.body` names a place inside it."
            ),
            "focus": (
                "Take each backticked path in `draft.instructions` and look for that place in "
                "`sample_state`. A path resolves when `sample_state` holds a value there. A "
                "draft that names no backticked path has no dangling path."
            ),
            "examples_of_true": [
                "The draft reads `ticket.priority` and `sample_state` has a ticket with only "
                "`body` and `sender`.",
            ],
            "examples_of_false": [
                "The draft reads `ticket.body` and `sample_state` has a ticket with a `body`.",
            ],
        },
        {
            "true": "At least one backticked path in `draft.instructions` names a place that "
                    "`sample_state` does not hold a value at.",
            "false": "Every backticked path in `draft.instructions` resolves to a value in "
                     "`sample_state`, or the draft names no backticked path.",
        },
    ),
    "needs_forbidden_reasoning": noul(
        {
            "question": (
                "Is there a step in answering `draft.instructions` that has to be computed "
                "rather than recognised -- specifically, a step from `cannot_compute`?"
            ),
            "cannot_compute": [
                "adding, subtracting, or comparing numbers",
                "counting how many items are present",
                "working out which of two dates is earlier, or how far apart they are",
                "interpreting hex, RGB, or assembly",
                "writing new text, a summary, or a rewritten value",
            ],
            "focus": (
                "Recognising meaning is not computing. Judging that a report sounds urgent, "
                "that a feature is unusable, that a message mentions a deadline, or that one "
                "description fits better than another are all recognition."
            ),
            "examples_of_true": [
                "How many days remain until the invoice is due?",
                "Is the total of the line items equal to the stated amount?",
                "Summarize the customer's complaint.",
            ],
            "examples_of_false": [
                "How severe is the problem reported in the ticket?",
                "Does the message mention a deadline?",
            ],
        },
        {
            "true": "Answering requires at least one step from `cannot_compute`.",
            "false": "Answering requires only recognising or comparing meaning in the state, "
                     "with no calculation to perform and no new text to produce.",
        },
    ),
}

CRITERIA_QUESTIONS: dict[str, dict] = {
    "criteria_conflicts": noul(
        {
            "question": (
                "Does any entry in `draft.criteria` state something that points the opposite "
                "way from `draft.instructions`?"
            ),
            "examples_of_true": [
                "A yes/no question whose `true` entry describes the 'no' case.",
                "An option described as covering something the instructions explicitly exclude.",
                "A level described as the least of a property, placed where the most belongs.",
            ],
        },
        {
            "true": "At least one entry in `draft.criteria` points the opposite way from "
                    "`draft.instructions`.",
            "false": "Every entry in `draft.criteria` points the same way as "
                     "`draft.instructions`, even where an entry is vague or thin.",
        },
    ),
    "criteria_off_target": noul(
        {
            "question": (
                "Do the entries in `draft.criteria` sort inputs by a different property from "
                "the one `draft.instructions` asks about?"
            ),
            "focus": (
                "This is about which property the entries measure. It is not about how clearly "
                "they are written, and not about an entry that states the opposite of the "
                "instructions."
            ),
        },
        {
            "true": "The entries sort inputs by some property other than the one the "
                    "instructions ask about.",
            "false": "The entries sort inputs by the same property the instructions ask about.",
        },
    ),
}

CHOICE_QUESTIONS: dict[str, dict] = {
    "input_matches_no_option": noul(
        {
            "question": (
                "Could a realistic input, of the kind someone would send to "
                "`draft.instructions`, belong to none of the options listed in "
                "`draft.criteria`?"
            ),
            "focus": (
                "An option covers an input when its name or description makes clear the input "
                "belongs there. A catch-all option such as 'other' or 'none of the above' "
                "covers anything the named options miss. Consider both inputs that are "
                "on-topic but outside every named option, and inputs that are off-topic."
            ),
        },
        {
            "true": "A realistic input exists that no option in `draft.criteria` covers.",
            "false": "Every realistic input is covered by at least one option, counting any "
                     "catch-all option.",
        },
    ),
    "options_overlap": noul(
        {
            "question": (
                "Is there a pair of options in `draft.criteria` that one realistic input could "
                "belong to equally well?"
            ),
            "focus": (
                "Judge the boundary as the descriptions draw it. Two options overlap when "
                "nothing in either description tells a reader which of the two an input on "
                "their shared edge belongs to."
            ),
        },
        {
            "true": "At least two options share an edge that their descriptions do not divide.",
            "false": "Each option's description marks off territory the others do not claim.",
        },
    ),
    "option_descriptions": score(
        {
            "question": (
                "How well do the entries in `draft.criteria` mark each option off from the "
                "others?"
            ),
            "context": (
                "Both an option's name and its description are sent to the model that answers "
                "the draft. A description may be a string, an object with labelled parts, or "
                "absent."
            ),
        },
        [
            "Every option carries a description, and any two that could be confused say "
            "explicitly what they are not for.",
            "Every option carries a description that states what it covers, and no two "
            "descriptions restate each other.",
            "Some options have no description, or a description that only repeats the option "
            "name in other words.",
            "The options are bare names with no descriptions, or the descriptions are too thin "
            "to tell any two of them apart.",
        ],
    ),
}

SCORE_QUESTIONS: dict[str, dict] = {
    "level_writing": score(
        {
            "question": (
                "How far does each entry in `draft.criteria` describe a situation an input "
                "could be matched against?"
            ),
            "context": (
                "`draft.criteria` is the ordered list of levels for a graded question. The "
                "model answering it sees each level's text on its own: never the level's "
                "position number, and never the text of its neighbours."
            ),
            "focus": (
                "A situation is something observable about an input. A degree word such as "
                "'moderate', 'somewhat', or 'high' names an amount without saying what it "
                "would look like."
            ),
        },
        [
            "Every level names a concrete situation: a state of affairs an input either shows "
            "or does not show.",
            "Most levels name situations, but at least one leans on a degree word or an "
            "abstract amount instead.",
            "Levels are mostly degree words, or are phrased against another level, such as "
            "'worse than the one before'.",
            "Levels carry no describable content at all: bare numbers, single adjectives, or "
            "labels such as 'low' and 'high' with nothing else.",
        ],
    ),
    "levels_multi_dimensional": noul(
        {
            "question": (
                "Does any entry in `draft.criteria` bring in a property that varies "
                "independently of the one thing the list as a whole is measuring?"
            ),
            "focus": (
                "First work out what single thing the list as a whole measures. A level may "
                "name several conditions together and still be one point on that single thing. "
                "A level is multi-dimensional only when it pulls in a property the rest of the "
                "list is not measuring at all."
            ),
            "examples_of_true": [
                "A list about employee quality with a level reading 'punctual and experienced "
                "and well liked' -- three properties that vary apart from each other.",
                "A list about how severe a problem is, where one level also requires that many "
                "customers are affected.",
            ],
            "examples_of_false": [
                "A list about how badly a user is blocked, with a level reading 'a feature is "
                "broken but a workaround exists' -- two conditions naming one point on that "
                "one scale.",
            ],
        },
        {
            "true": "At least one level brings in a property the rest of the list does not "
                    "measure.",
            "false": "Every level is a point on the one thing the list measures, however many "
                     "conditions it names to pin that point down.",
        },
    ),
    "levels_unordered": noul(
        {
            "question": (
                "Is `draft.criteria` out of order as a scale -- do its entries, in the order "
                "given, fail to run from the least of one property to the most of it?"
            ),
            "focus": (
                "The entries form a scale when each describes more of the same property than "
                "the one before. They fail to when an entry would sit better earlier in the "
                "list, or when an entry describes a different kind of thing altogether."
            ),
            "examples_of_true": [
                "A severity list running 'cosmetic', then 'blocking with no workaround', then "
                "'degraded but a workaround exists' -- the last two belong the other way round.",
                "A completeness list whose final entry describes who submitted the record "
                "rather than how complete it is.",
            ],
            "examples_of_false": [
                "A severity list running 'cosmetic', then 'degraded but a workaround exists', "
                "then 'blocking with no workaround', then 'cannot use the product at all'.",
            ],
        },
        {
            "true": "The entries do not run from the least of one property to the most of it.",
            "false": "The entries run from the least of one property to the most of it, in the "
                     "order they are given.",
        },
    ),
}

NOUL_QUESTIONS: dict[str, dict] = {
    "high_means_absence": noul(
        {
            "question": (
                "In `draft.instructions`, would a high probability mean that something is "
                "missing, absent, or did not happen?"
            ),
            "context": (
                "This answer shape returns one probability that the condition named in the "
                "instructions holds. Code reading it is easier to keep correct when a high "
                "value reports presence rather than absence."
            ),
            "focus": (
                "Read the condition's own wording, looking for negations such as 'does not', "
                "'fails to', 'is missing', or 'no longer'."
            ),
        },
        {
            "true": "A high probability would report an absence, or a failure to do something.",
            "false": "A high probability would report that something is present, or did happen.",
        },
    ),
}

ALL_QUESTIONS: dict[str, dict] = {
    **UNIVERSAL_QUESTIONS,
    **CRITERIA_QUESTIONS,
    **CHOICE_QUESTIONS,
    **SCORE_QUESTIONS,
    **NOUL_QUESTIONS,
}

CRITIC_SET = register(
    QuestionSet(
        name="question_critic",
        version="1.0.0",
        description=(
            "Judges a drafted TypeSafe question against the documented craft rules: "
            "atomicity, primitive fit, literalness, and criteria quality."
        ),
        questions=ALL_QUESTIONS,
        thresholds={},          # advisory tool: nothing here gates anything
        gates_irreversible=False,
        validated_on=None,      # bands below are provisional, see PROVISIONAL_BANDS
    )
)


# --- Which checks apply, and what each one means --------------------------------------------


@dataclass(frozen=True)
class Check:
    """One judgment, plus what its answer means and what to do about it."""

    qid: str
    title: str
    fix: str
    applies_to: tuple[str, ...] = PRIMITIVES
    requires_criteria: bool = False
    requires_sample_state: bool = False
    advisory: bool = False          # true = a style note, not a defect
    ranked: bool = True             # false = read for detail, not as a severity signal
    skipped_because: str = ""       # shown in the report when the check could not be asked


CHECKS: tuple[Check, ...] = (
    Check(
        qid="hidden_judgments",
        title="Hides more than one judgment",
        fix="Split into one question per judgment and combine the answers in code. Broad "
            "questions return one number you cannot trace or tune.",
    ),
    Check(
        qid="best_primitive",
        title="Primitive that fits the answer's meaning",
        fix="",
        ranked=False,
    ),
    Check(
        qid="declared_primitive_wrong",
        title="Declared primitive does not carry the answer",
        fix="Change the declared type. A Choice settles which option wins (relative); a Noul "
            "settles whether a condition holds at all (absolute); a Score settles where on an "
            "ordered scale something sits. They do not share a scale.",
    ),
    Check(
        qid="unstated_context",
        title="Not self-contained; relies on unwritten context",
        fix="Write the missing rule into the instruction. Question IDs, variable names, and "
            "surrounding code are never sent to the model -- if you find yourself explaining "
            "what you meant, that explanation is the missing half of the instruction.",
    ),
    Check(
        qid="dangling_state_path",
        title="A backticked path points at nothing in the sample state",
        fix="Correct the path, or add the value to the state the caller builds. A path the "
            "state does not hold is read literally as a missing value, and the answer is then "
            "about an absence rather than about your question.",
        requires_sample_state=True,
        skipped_because="no sample state was supplied, so backticked paths could not be "
                        "resolved; pass one with --state to check them",
    ),
    Check(
        qid="needs_forbidden_reasoning",
        title="Requires reasoning Jev is documented to fail at",
        fix="Do the arithmetic, counting, or date comparison in code and send Jev only the "
            "semantic part. If the answer must be generated text, this is not a TypeSafe "
            "question at all.",
    ),
    Check(
        qid="criteria_conflicts",
        title="Criteria contradict the instruction",
        fix="Rewrite the offending entry so it points the same way as the instruction. "
            "Criteria are read as an extension of the instruction; a `true` that means 'no' "
            "measurably degrades the answer.",
        requires_criteria=True,
    ),
    Check(
        qid="criteria_off_target",
        title="Criteria measure a different property than the instruction asks about",
        fix="Decide which property you actually want, then rewrite whichever half is wrong so "
            "instruction and criteria name the same one.",
        requires_criteria=True,
    ),
    Check(
        qid="input_matches_no_option",
        title="An input can match no option",
        fix="Add an escape option ('other', 'none of the above'). A Choice's probabilities sum "
            "to 1, so without one it can never say 'none of these fit' and will manufacture a "
            "false positive.",
        applies_to=("choice",),
    ),
    Check(
        qid="options_overlap",
        title="Two options claim the same input",
        fix="Give the confusable options a shared `not_for` field naming the other. This is the "
            "sharpest tool available for a blurred boundary.",
        applies_to=("choice",),
    ),
    Check(
        qid="option_descriptions",
        title="Option descriptions do not separate the options",
        fix="Give every option a description, and use the same field names across options "
            "(`what` / `not_for` / `examples`) so the model compares like with like. Examples "
            "steer only when they resemble real inputs.",
        applies_to=("choice",),
    ),
    Check(
        qid="level_writing",
        title="Levels describe degrees, not situations",
        fix="Rewrite each level as a situation the state can be matched against -- 'Broken or "
            "degraded feature, but a workaround exists', not 'moderately severe'. Measured: "
            "bare-number levels scored 0.57 at confidence 0.35 where described levels scored "
            "0.0 at confidence 1.0.",
        applies_to=("score",),
    ),
    Check(
        qid="levels_multi_dimensional",
        title="A level measures more than one dimension",
        fix="Split into one Score per dimension and combine in code. An input high on one "
            "dimension and low on another cannot be placed on a level that names both.",
        applies_to=("score",),
    ),
    Check(
        qid="levels_unordered",
        title="Levels do not form one ordered progression",
        fix="Reorder so each level is more of the same property than the one before, or -- if "
            "the outcomes are genuinely discrete -- make this a Choice, or make the levels the "
            "available actions so routing becomes round-to-nearest.",
        applies_to=("score",),
    ),
    Check(
        qid="high_means_absence",
        title="A high probability reports an absence",
        fix="Consider inverting so a high value means the thing is present; code reading it "
            "then carries no negation. Not always wrong -- but double negatives cost accuracy, "
            "so verify both phrasings on real inputs.",
        applies_to=("noul",),
        advisory=True,
    ),
)

_CHECKS_BY_ID = {c.qid: c for c in CHECKS}


def applicable_checks(
    draft_type: str, *, has_criteria: bool = True, has_sample_state: bool = False
) -> tuple[list[Check], list[Check]]:
    """Split this draft's checks into (asked, skipped).

    Selection is deterministic -- every input here is known before the call -- so this is code
    doing code's job, not a threshold. Everything asked still travels in one request.
    """
    if draft_type not in PRIMITIVES:
        raise ValueError(f"Unknown draft type {draft_type!r}; expected one of {PRIMITIVES}")
    asked: list[Check] = []
    skipped: list[Check] = []
    for check in CHECKS:
        if draft_type not in check.applies_to:
            continue
        if check.requires_criteria and not has_criteria:
            continue
        # A check whose evidence is absent is reported as unasked, never scored as if the
        # evidence were there and damning.
        if check.requires_sample_state and not has_sample_state:
            skipped.append(check)
            continue
        asked.append(check)
    return asked, skipped


def questions_for(
    draft_type: str, *, has_criteria: bool = True, has_sample_state: bool = False
) -> dict[str, dict]:
    """The applicable subset of `CRITIC_SET.questions` for one draft."""
    asked, _ = applicable_checks(
        draft_type, has_criteria=has_criteria, has_sample_state=has_sample_state
    )
    return {c.qid: CRITIC_SET.questions[c.qid] for c in asked}


# --- Deterministic structural checks --------------------------------------------------------


@dataclass
class Structural:
    """A defect found by code, not by a judgment. Certain, so it outranks every judgment."""

    level: str          # "error" | "warning"
    message: str
    fix: str

    def to_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "fix": self.fix}


_ESCAPE_NAMES = {
    "other", "others", "none", "none_of_the_above", "no_match", "unknown", "unclear",
    "unsure", "n_a", "na", "not_applicable", "neither", "something_else", "else", "misc",
    "unrelated", "out_of_scope", "cannot_tell",
}
_ESCAPE_PHRASES = (
    "none of the above", "anything else", "does not fit", "doesn't fit", "no option",
    "not covered", "everything else", "catch-all", "catch all", "none of these",
)
_BACKTICK = re.compile(r"`[^`\n]+`")
_BARE_NUMBER = re.compile(r"^\s*[\d.]+\s*$")


def _strings(obj: Any) -> Iterable[str]:
    """Every string anywhere in a nested instructions/criteria value."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
        for item in obj:
            yield from _strings(item)


def _looks_like_escape(name: str, description: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    if normalized in _ESCAPE_NAMES:
        return True
    # Compound escape names are common -- `not_a_judgment`, `other_topic`, `none_apply`.
    # This stays a hint: `input_matches_no_option` is the judgment that actually decides
    # whether an escape is needed, and it is reported alongside.
    if normalized.startswith(("not_", "no_", "none_", "other_", "unknown_")):
        return True
    if any(normalized.endswith("_" + tail) for tail in ("other", "none", "unknown", "else")):
        return True
    blob = " ".join(_strings(description)).lower()
    return any(phrase in blob for phrase in _ESCAPE_PHRASES)


def structural_checks(draft: Mapping[str, Any]) -> list[Structural]:
    """Everything a regex and a `len()` can settle. No model call."""
    out: list[Structural] = []
    draft_type = draft.get("type")
    instructions = draft.get("instructions")
    criteria = draft.get("criteria")

    if draft_type not in PRIMITIVES:
        out.append(Structural(
            "error", f"`type` is {draft_type!r}; must be one of {list(PRIMITIVES)}.",
            "Set `type` to choice, score, or noul.",
        ))
        return out

    if instructions is None or (isinstance(instructions, str) and not instructions.strip()):
        out.append(Structural(
            "error", "`instructions` is empty.",
            "Write the complete question in `instructions`. The question ID is never sent to "
            "the model, so the instruction has to carry the whole question on its own.",
        ))
    else:
        text = " ".join(_strings(instructions))
        if not _BACKTICK.search(text):
            out.append(Structural(
                "warning", "No backticked state path in `instructions`.",
                "Point at the state you mean with a backticked path such as "
                "`ticket.messages[0].text`, including the backticks. Without one it is "
                "ambiguous which part of a structured state the question is about.",
            ))

    if draft_type == "score":
        levels = criteria if isinstance(criteria, Sequence) and not isinstance(
            criteria, (str, bytes, Mapping)) else None
        if levels is None:
            out.append(Structural(
                "error", "A Score's `criteria` must be an ordered array of levels.",
                "Replace `criteria` with a low-to-high list of level descriptions.",
            ))
        else:
            if not 2 <= len(levels) <= 10:
                out.append(Structural(
                    "error", f"A Score takes 2-10 levels; this draft has {len(levels)}.",
                    "Merge or split levels to land inside the documented range.",
                ))
            bare = [i for i, lv in enumerate(levels)
                    if isinstance(lv, str) and _BARE_NUMBER.fullmatch(lv)]
            if bare:
                out.append(Structural(
                    "error", f"Levels {bare} are bare numbers.",
                    "Levels are judged independently and the model never sees a level's "
                    "index, so a number carries no information. Describe the situation.",
                ))

    if draft_type == "choice":
        if not isinstance(criteria, Mapping) or not criteria:
            out.append(Structural(
                "error", "A Choice's `criteria` must be a non-empty map of option -> description.",
                "Give the full option list, not a shortlist. Options cost a few tokens each "
                "and up to 255 are allowed.",
            ))
        else:
            if len(criteria) > 255:
                out.append(Structural(
                    "error", f"A Choice takes at most 255 options; this draft has {len(criteria)}.",
                    "Split the taxonomy into levels and walk it with one Choice per level.",
                ))
            if not any(_looks_like_escape(k, v) for k, v in criteria.items()):
                out.append(Structural(
                    "warning", "No escape option found among the option names or descriptions.",
                    "Add 'other' or 'none of the above' if an input might match nothing. See "
                    "the `input_matches_no_option` judgment below for whether one is needed.",
                ))

    if draft_type == "noul":
        if criteria is not None:
            if not isinstance(criteria, Mapping):
                out.append(Structural(
                    "error", "A Noul's `criteria` must be a map with keys `true` and `false`.",
                    "Use {'true': ..., 'false': ...}, or drop `criteria` entirely.",
                ))
            else:
                unknown = sorted(set(criteria) - {"true", "false"})
                if unknown:
                    out.append(Structural(
                        "error", f"A Noul's `criteria` accepts only true/false; found {unknown}.",
                        "Move the extra keys into `instructions`, or make this a Choice.",
                    ))
        if isinstance(instructions, str) and "confidence" in instructions.lower():
            out.append(Structural(
                "warning", "The instruction mentions confidence, but a Noul answer has none.",
                "A Noul returns one probability and no confidence field. If you need a "
                "confidence value, use a Choice or a Score.",
            ))

    return out


# --- Findings and report ---------------------------------------------------------------------


def band(signal: float) -> str:
    for floor, label in PROVISIONAL_BANDS:
        if signal >= floor:
            return label
    return "low"


@dataclass
class Finding:
    qid: str
    title: str
    signal: float               # 0-1, higher = more broken
    primitive: str
    fix: str
    advisory: bool = False
    confidence: float | None = None
    detail: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)

    @property
    def band(self) -> str:
        return band(self.signal)

    def to_dict(self) -> dict:
        return {
            "id": self.qid,
            "title": self.title,
            "signal": round(self.signal, 4),
            "band_provisional": self.band,
            "primitive": self.primitive,
            "advisory": self.advisory,
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "detail": self.detail,
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "fix": self.fix,
        }


@dataclass
class Report:
    """What the critic found about one draft, ranked worst-first."""

    draft: Mapping[str, Any]
    name: str
    structural: list[Structural]
    findings: list[Finding]
    best_primitive: str | None = None
    best_primitive_confidence: float | None = None

    #: (check id, why) for checks this draft was eligible for but whose evidence was absent.
    #: Reported, never silently scored -- an unasked check is not a passing one.
    not_checked: list[tuple[str, str]] = field(default_factory=list)

    input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def worst(self) -> Finding | None:
        ranked = [f for f in self.findings if not f.advisory]
        return ranked[0] if ranked else (self.findings[0] if self.findings else None)

    @property
    def has_errors(self) -> bool:
        return any(s.level == "error" for s in self.structural)

    def signal(self, qid: str) -> float:
        for finding in self.findings:
            if finding.qid == qid:
                return finding.signal
        raise KeyError(f"No finding {qid!r} in this report. Present: "
                       f"{[f.qid for f in self.findings]}")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "draft_type": self.draft.get("type"),
            "bands_are_provisional": True,
            "structural": [s.to_dict() for s in self.structural],
            "best_primitive": self.best_primitive,
            "best_primitive_confidence": (
                None if self.best_primitive_confidence is None
                else round(self.best_primitive_confidence, 4)
            ),
            "findings": [f.to_dict() for f in self.findings],
            "not_checked": [{"id": qid, "why": why} for qid, why in self.not_checked],
            "usage": {
                "input_tokens": self.input_tokens,
                "cost_usd": round(self.cost_usd, 8),
                "latency_ms": self.latency_ms,
            },
        }

    def render(self) -> str:
        lines = [f"question-critic  --  {self.name}  [{self.draft.get('type')}]", "=" * 72]

        if self.structural:
            lines.append("\nSTRUCTURAL (settled in code, not by a judgment)")
            for s in sorted(self.structural, key=lambda x: x.level != "error"):
                lines.append(f"  [{s.level.upper():7}] {s.message}")
                lines.append(f"            fix: {s.fix}")

        if self.best_primitive:
            declared = self.draft.get("type")
            mark = "matches declared" if self.best_primitive == declared else "DIFFERS from declared"
            conf = self.best_primitive_confidence
            lines.append(
                f"\nPRIMITIVE  best fit = {self.best_primitive} "
                f"(confidence {conf:.2f}) -- {mark} ({declared})"
            )

        lines.append("\nRANKED FINDINGS  (signal 0-1, higher = more broken; bands provisional)")
        if not self.findings:
            lines.append("  none -- no judgments applied to this draft")
        for i, f in enumerate(self.findings, 1):
            conf = "" if f.confidence is None else f"  conf {f.confidence:.2f}"
            flag = "  (advisory)" if f.advisory else ""
            lines.append(f"  {i}. [{f.band:6}] {f.signal:.2f}{conf}  {f.title}{flag}")
            if f.detail:
                lines.append(f"      {f.detail}")
            lines.append(f"      FIX: {f.fix}")

        if self.not_checked:
            lines.append("\nNOT CHECKED  (evidence absent -- unasked, not passed)")
            for qid, why in self.not_checked:
                lines.append(f"  {qid}: {why}")

        lines.append(
            f"\n{self.input_tokens} input tokens, ${self.cost_usd:.8f}, {self.latency_ms} ms"
        )
        lines.append(
            "Bands are provisional and unvalidated; nothing here gates an action. "
            "Read the signal, not the band."
        )
        return "\n".join(lines)


# --- The critic ------------------------------------------------------------------------------


def _has_criteria(draft: Mapping[str, Any]) -> bool:
    criteria = draft.get("criteria")
    return criteria is not None and len(criteria) > 0


def critique(
    draft: Mapping[str, Any],
    *,
    client: TypeSafeClient | None = None,
    purpose: str | None = None,
    name: str = "draft",
    sample_state: Any = None,
) -> Report:
    """Judge one drafted question. One request; ranked, actionable findings.

    `sample_state` is an example of the application state the draft will really run against.
    Supply it whenever the draft uses backticked paths: without it the critic cannot tell a
    path that resolves from one that dangles, and says so rather than guessing.
    """
    structural = structural_checks(draft)
    draft_type = draft.get("type")

    if draft_type not in PRIMITIVES:
        return Report(draft=draft, name=name, structural=structural, findings=[])

    state: dict[str, Any] = {
        "draft": {
            "type": draft_type,
            "instructions": draft.get("instructions"),
            "criteria": draft.get("criteria"),
        },
        "purpose": purpose or "not stated",
    }
    if sample_state is not None:
        state["sample_state"] = sample_state

    asked, skipped = applicable_checks(
        draft_type,
        has_criteria=_has_criteria(draft),
        has_sample_state=sample_state is not None,
    )
    selected = {c.qid: CRITIC_SET.questions[c.qid] for c in asked}
    client = client or TypeSafeClient()
    result = client.ask(
        state=state, questions=selected, model=CRITIC_SET.model, tag=TAG
    )

    best_primitive = result.choice("best_primitive")
    best_conf = result.confidence("best_primitive")

    findings: list[Finding] = []
    for qid in selected:
        check = _CHECKS_BY_ID[qid]
        if not check.ranked:
            continue
        kind = CRITIC_SET.questions[qid]["type"]
        if kind == "noul":
            signal, confidence = result.noul(qid), None
        else:
            signal, confidence = result.normalized_score(qid), result.confidence(qid)

        fix, detail = check.fix, None

        # Composition in code: the Choice says which primitive fits best (relative), the Noul
        # says whether the declared one is actually wrong (absolute). They do not share a
        # scale, so the suggestion is only attached when the absolute check agrees there is a
        # problem; otherwise it is reported as context.
        if qid == "declared_primitive_wrong":
            if best_primitive == "not_a_judgment":
                detail = ("Best-fit primitive is `not_a_judgment`: no Choice, Score, or Noul "
                          "can carry this answer.")
                fix = ("Do this in code, or hand it to a generative model. TypeSafe answers "
                       "typed questions; it does not generate text, numbers, or lists. " + fix)
            elif best_primitive != draft_type:
                detail = f"Best-fit primitive is `{best_primitive}`, declared is `{draft_type}`."
                fix = f"Change `type` to `{best_primitive}`. " + fix
            else:
                # The Choice is relative and the Noul absolute; when they disagree the Choice
                # is the weaker evidence, so do not tell the agent to change a type that the
                # best-fit judgment endorses.
                detail = (
                    f"Best-fit primitive agrees with the declared `{draft_type}`, so any "
                    "signal here is about what the question asks, not which type it declares."
                )
                fix = (
                    f"Leave `type` as `{draft_type}` -- the best-fit judgment endorses it. If "
                    "this signal is high anyway, the instruction is asking for something no "
                    "typed answer carries cleanly; tighten the instruction rather than the type."
                )

        findings.append(Finding(
            qid=qid, title=check.title, signal=signal, primitive=kind, fix=fix,
            advisory=check.advisory, confidence=confidence, detail=detail,
            probabilities=result.probabilities(qid) if kind != "noul" else {},
        ))

    # Rank: real defects before advisory notes, then worst signal first.
    findings.sort(key=lambda f: (f.advisory, -f.signal))

    return Report(
        draft=draft, name=name, structural=structural, findings=findings,
        best_primitive=best_primitive, best_primitive_confidence=best_conf,
        not_checked=[(c.qid, c.skipped_because) for c in skipped],
        input_tokens=result.input_tokens, cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )


def critique_many(
    drafts: Mapping[str, Mapping[str, Any]],
    *,
    client: TypeSafeClient | None = None,
    purpose: str | None = None,
    sample_state: Any = None,
) -> list[Report]:
    """Critique a whole `questions` dict. Each draft is its own state, so these fan out.

    One `sample_state` covers every draft in the dict, which matches how a QuestionSet runs:
    its questions all read the same application state.
    """
    client = client or TypeSafeClient()
    return [
        critique(d, client=client, purpose=purpose, name=n, sample_state=sample_state)
        for n, d in drafts.items()
    ]


# --- CLI -------------------------------------------------------------------------------------


def _load(raw: str) -> dict[str, Mapping[str, Any]]:
    """Accept one draft, a list of drafts, or a whole `questions` dict keyed by ID."""
    data = json.loads(raw)
    if isinstance(data, list):
        return {f"draft[{i}]": d for i, d in enumerate(data)}
    if not isinstance(data, Mapping):
        raise ValueError("Expected a JSON object or array")
    if "type" in data and ("instructions" in data or "criteria" in data):
        return {"draft": data}
    drafts = {k: v for k, v in data.items() if isinstance(v, Mapping) and "type" in v}
    if not drafts:
        raise ValueError(
            "No drafts found. Expected {type, instructions, criteria}, a list of those, or a "
            "questions dict whose values have that shape."
        )
    return drafts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="question-critic",
        description="Judge drafted TypeSafe questions against the documented craft rules.",
    )
    parser.add_argument("--file", help="JSON file with the draft(s). Omit to read stdin.")
    parser.add_argument("--purpose", help="What the calling code does with the answer.")
    parser.add_argument("--state", help="JSON file holding an example of the application state "
                                        "the draft will run against. Supply it when the draft "
                                        "uses backticked paths, so they can be resolved.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Machine-readable output.")
    parser.add_argument("--model", help="Override the pinned model (thresholds are unaffected; "
                                        "this tool has none).")
    args = parser.parse_args(argv)

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    drafts = _load(raw)

    sample_state = (
        json.loads(Path(args.state).read_text(encoding="utf-8")) if args.state else None
    )

    client = TypeSafeClient(model=args.model) if args.model else TypeSafeClient()
    reports = critique_many(
        drafts, client=client, purpose=args.purpose, sample_state=sample_state
    )

    if args.as_json:
        print(json.dumps({"reports": [r.to_dict() for r in reports]}, indent=2))
    else:
        print("\n\n".join(r.render() for r in reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
