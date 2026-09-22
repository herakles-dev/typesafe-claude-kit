"""Question sets for `tools/jaggedness_screen.py` -- design-time jaggedness triage.

Screens a *proposed* TypeSafe design against the nine documented `jev-1.13` failure modes
(`reference/model-jaggedness/jev-1.13.md`) before anyone writes the integration.

Two sets, because the modes do not all live at the same grain:

  * `jaggedness_screen.question` -- modes 1, 2, 3, 4, 7, 9 plus a consequence Score. These are
    properties of a *single* question's wording. One request per design question, fanned out:
    each request's state contains exactly one question, so the screen never has to reason
    about `questions[3].criteria` -- that indirection would be failure mode #4 in the screen
    itself. This is the reranking exception to the batching rule (AGENTS.md rule 1): the
    subject differs per request, so there is nothing to share.

  * `jaggedness_screen.design` -- modes 5, 6, 8 plus a consequence Score. These are properties
    of the state, its provenance, and the policy code that reads the answers. One request.

Every question is a **Noul**, one per mode, because the modes are independent and several fire
at once -- a Choice would force exactly one winner and hide the rest. Orientation is uniform:
a high `noul` always means "the risk is present", except for
`mode6_criteria_declare_untrusted`, which is deliberately worded positively and inverted in
code. Failure mode #7 says a Noul whose `true` leans toward "no" performs worse, so the
inversion belongs on the code side, never in the wording.

Four questions here are **not** one of the nine upstream modes. They came out of red-teaming
real integrations and each earned its place on a measured paired separation, recorded in
`tools/jaggedness_screen.py`:

  * `attacker_settable_premise` (per question) -- the question is well posed and the model
    answers it correctly, but the field it reads is authored by someone outside the system, so
    that outsider sets the answer. Not injection: nothing is tricked.
  * `correlated_evidence` (per design) -- two questions whose answers travel together in
    ordinary traffic. Accuracy on happy-path data cannot tell which one the decision rests on,
    so a validation set drawn from that traffic proves nothing about either.
  * `unbanded_cutoff` (per design) -- a probability turned straight into an action at one
    number, with no middle range held for a person.

The fourth, `unmeasured_threshold`, is not here at all: it is decided in code without an API
call, because whether a policy names a number and whether its provenance is declared are both
things `re` and a dict lookup settle exactly.

`gates_irreversible=False`: the screen is advisory. It ranks, it does not gate. There are no
thresholds here on purpose -- any cutoff over these numbers is a hypothesis until measured
(`typesafe-calibrator`, AGENTS.md rule 7).
"""

from __future__ import annotations

from . import QuestionSet, register
from typesafe_client import noul, score

__all__ = ["PER_QUESTION", "PER_DESIGN", "TAG", "CONSEQUENCE_LEVELS"]

#: Every call this tool makes carries this tag in the usage log ($TYPESAFE_USAGE_LOG, default
#: ~/.typesafe/usage.jsonl).
TAG = "jaggedness-screen"

#: Four ordered levels, each describing a *situation* (MASTERY.md section 3 -- degree words
#: and bare numbers measurably collapse accuracy). Shared by both sets so the two consequence
#: numbers are on the same scale and may be compared after `normalized_score`.
CONSEQUENCE_LEVELS = [
    "Nothing acts on the answer. It is logged, displayed, or filed for a person to read.",
    "The answer changes what a person is shown or the order work is presented in. A person "
    "still makes the decision and can ignore it.",
    "Code acts on the answer without a person in the loop, and the action can be undone by "
    "re-running it, reverting a file, or editing a record back.",
    "Code acts on the answer without a person in the loop and the action is hard to undo: "
    "data is deleted or overwritten, money moves, a message leaves the system, or a process "
    "with write access is launched.",
]


PER_QUESTION = register(QuestionSet(
    name="jaggedness_screen.question",
    version="1.1.0",
    description=(
        "Screens one proposed question against the jev-1.13 failure modes that are properties "
        "of its wording (1 literal reading, 2 math/counting, 3 dates, 4 indirection, "
        "7 criteria conflict, 9 generation), plus whether an outsider authors the field "
        "it reads, plus the consequence of getting it wrong."
    ),
    gates_irreversible=False,
    thresholds={},
    questions={
        # --- mode 1 -----------------------------------------------------------------------
        "mode1_literal_reading": noul(
            {
                "question": (
                    "Read `question.instructions` and `question.criteria` at face value, as a "
                    "reader who follows the words exactly and supplies nothing the designer "
                    "left unsaid. Is there a state that would satisfy the words while "
                    "plainly missing what the designer wants?"
                ),
                "focus": (
                    "Scoping words, negations, and conditions that are implied rather than "
                    "written. Boundary cases that the criteria do not settle."
                ),
            },
            {
                "true": (
                    "A condition the right answer depends on is implied rather than written "
                    "down, or a scoping word or negation could be read more than one way, so "
                    "the literal reading and the intended reading can disagree."
                ),
                "false": (
                    "Every condition the answer depends on is written out in the instructions "
                    "or the criteria, boundary cases included. A literal reading and the "
                    "intended reading give the same answer."
                ),
            },
        ),
        # --- mode 2 -----------------------------------------------------------------------
        "mode2_math_or_counting": noul(
            {
                "question": (
                    "Does answering `question.instructions` require the model itself to count "
                    "things, do arithmetic, or judge raw numeric values?"
                ),
                "focus": (
                    "Counting items, characters, or occurrences. Adding, subtracting, or "
                    "comparing magnitudes. Hex codes, RGB triples, byte offsets, assembly, or "
                    "any other numeric encoding the model must interpret as a quantity."
                ),
            },
            {
                "true": (
                    "The answer depends on a tally, a calculation, or a comparison of raw "
                    "numeric or encoded values that the model would have to perform."
                ),
                "false": (
                    "The answer is a semantic judgment. Any number involved is already "
                    "computed and labelled in the state, and the model only has to read it."
                ),
            },
        ),
        # --- mode 3 -----------------------------------------------------------------------
        "mode3_date_or_time": noul(
            {
                "question": (
                    "Does answering `question.instructions` require the model itself to place "
                    "dates or times in order, measure the gap between them, or decide whether "
                    "one falls inside a period?"
                ),
                "focus": (
                    "Which came first, how long between, is it inside the window, which "
                    "quarter or billing period, what weekday, relative references such as "
                    "'the Friday after next'."
                ),
            },
            {
                "true": (
                    "The answer depends on ordering, differencing, or bounding dates or times "
                    "that the model would have to treat as quantities."
                ),
                "false": (
                    "The question asks only whether a date is present or what it says, or it "
                    "involves no dates at all. Any ordering or interval is computed in code."
                ),
            },
        ),
        # --- mode 4 -----------------------------------------------------------------------
        "mode4_indirection": noul(
            {
                "question": (
                    "Does answering `question.instructions` take more than one hop of "
                    "reasoning from the state to the answer?"
                ),
                "focus": (
                    "A property of a property. A chain that must pass through one part of the "
                    "state to find the part that holds the answer. Double negatives. "
                    "Conditions stacked on conditions."
                ),
            },
            {
                "true": (
                    "The answer requires reaching through one fact to get to another, or "
                    "untangling a negation of a negation, before the judgment can be made."
                ),
                "false": (
                    "The relevant part of the state is named directly and the judgment is made "
                    "on it in one step."
                ),
            },
        ),
        # --- mode 7 -----------------------------------------------------------------------
        "mode7_criteria_conflict": noul(
            {
                "question": (
                    "Do `question.criteria` ask for something different from what "
                    "`question.instructions` asks for?"
                ),
                "focus": (
                    "Criteria that describe a narrower, wider, or simply different condition "
                    "than the instruction. For a Noul, a `true` description that a reader "
                    "would naturally answer 'no' to, or criteria written in the opposite "
                    "polarity from the instruction."
                ),
            },
            {
                "true": (
                    "The criteria and the instruction point at different conditions, or the "
                    "polarity is crossed so that `true` reads as the negative answer."
                ),
                "false": (
                    "The criteria read as a continuation of the instruction, in the same "
                    "direction, settling exactly the condition the instruction names."
                ),
            },
        ),
        # --- mode 9 -----------------------------------------------------------------------
        "mode9_generation": noul(
            {
                "question": (
                    "Does `question.instructions` ask the model to produce a value of its own, "
                    "rather than pick one from options the calling code has already listed?"
                ),
                "focus": (
                    "Writing a summary, name, label, phrase, or extracted string. Filling in a "
                    "value that appears nowhere in `question.criteria`."
                ),
            },
            {
                "true": (
                    "The answer the design wants is text or a value the model would have to "
                    "compose or transcribe, not one of an enumerated set."
                ),
                "false": (
                    "The answer is a selection from a set the code enumerated, a position on "
                    "described levels, or a yes/no probability."
                ),
            },
        ),
        # --- attacker-settable premise (not an upstream mode; see module docstring) --------
        # Deliberately asks who *authors* the field, never whether the text looks hostile.
        # A correct answer about attacker-written text is still an attacker-set answer, and
        # asking about hostility would collapse this into mode 6.
        "attacker_settable_premise": noul(
            {
                "question": (
                    "Does answering `question.instructions` require reading a part of the "
                    "state that someone outside this system wrote, or chose the contents of?"
                ),
                "focus": (
                    "Find the part of `design.state_the_model_will_receive` that the "
                    "instruction names, then ask who authors that part: this system's own "
                    "code, records and observations, or a user, a customer, a third-party "
                    "service, a scraped page, or another model. Judge who writes the text, "
                    "not whether the text looks hostile."
                ),
            },
            {
                "true": (
                    "The instruction reads a part of the state that a person or service "
                    "outside this system authors, so whoever writes that text decides the "
                    "answer."
                ),
                "false": (
                    "The instruction reads only parts of the state that this system's own "
                    "code produced from its own records and its own observations."
                ),
            },
        ),
        # --- consequence ------------------------------------------------------------------
        "consequence": score(
            {
                "question": (
                    "Suppose this question is answered wrong and the calling code acts on it "
                    "exactly as `question.used_for` describes. Where does the damage land?"
                ),
                "focus": (
                    "Judge by what the code does with the answer, not by how likely the wrong "
                    "answer is."
                ),
            },
            CONSEQUENCE_LEVELS,
        ),
    },
))


PER_DESIGN = register(QuestionSet(
    name="jaggedness_screen.design",
    version="1.1.0",
    description=(
        "Screens a whole proposed design against the jev-1.13 failure modes that are "
        "properties of the state and the policy code (5 irrelevant detail, 6 adversarial "
        "content, 8 false structural invariants), plus correlated evidence and unbanded "
        "cutoffs, plus the consequence of acting on it."
    ),
    gates_irreversible=False,
    thresholds={},
    questions={
        # --- mode 5 -----------------------------------------------------------------------
        "mode5_irrelevant_state": noul(
            {
                "question": (
                    "Does `state_description` describe material that none of the questions in "
                    "`questions_asked` needs in order to be answered?"
                ),
                "focus": (
                    "Fields, documents, transcripts, file listings, or history that are "
                    "present in the state but that no listed question reads."
                ),
            },
            {
                "true": (
                    "The state carries content beyond what the listed questions ask about -- "
                    "extra records, full documents where a field would do, or history kept "
                    "for context rather than for a specific question."
                ),
                "false": (
                    "Every part of the described state is read by at least one of the listed "
                    "questions. Nothing is there as background."
                ),
            },
        ),
        "mode5_state_is_large": noul(
            {
                "question": (
                    "As described in `state_description`, is the state large -- thousands of "
                    "words, whole documents, long lists, or many records per request?"
                ),
                "focus": "Size as described, not whether the content is relevant.",
            },
            {
                "true": "Each request would carry a substantial body of text or many records.",
                "false": "Each request carries a handful of short fields.",
            },
        ),
        # --- mode 6 -----------------------------------------------------------------------
        # Two halves. `carries_foreign_text` is an independent read on provenance that does
        # not trust the design's own `state_source` declaration. `criteria_declare_untrusted`
        # is worded positively (high = handled) and inverted in code -- see module docstring.
        "mode6_carries_foreign_text": noul(
            {
                "question": (
                    "Does the state described in `state_description` include text that "
                    "originated outside the system making the request?"
                ),
                "focus": (
                    "Text written by a user, pasted by a user, scraped from a page or feed, "
                    "received from a third party, or produced by another model. Anyone who "
                    "can put text into that state can put instructions into it."
                ),
            },
            {
                "true": (
                    "At least one part of the state is text that someone outside this system "
                    "authored or influenced."
                ),
                "false": (
                    "Every part of the state is generated by this system's own code from its "
                    "own records."
                ),
            },
        ),
        "mode6_criteria_declare_untrusted": noul(
            {
                "question": (
                    "Do the questions listed in `questions_asked` state anywhere -- in an "
                    "instruction or in a criterion -- that the text in the state is data to be "
                    "judged and must not be obeyed as an instruction?"
                ),
                "focus": (
                    "An explicit statement that the state is untrusted, that text inside it "
                    "claiming its own classification carries no weight, or that instructions "
                    "found in the state are to be treated as evidence rather than followed."
                ),
            },
            {
                "true": (
                    "At least one listed question says outright that the state's text is "
                    "untrusted data and is not to be followed."
                ),
                "false": (
                    "No listed question says anything about the trustworthiness of the text "
                    "in the state."
                ),
            },
        ),
        # --- mode 8 -----------------------------------------------------------------------
        "mode8_invariant_reliance": noul(
            {
                "question": (
                    "Does `code_policy` rely on a numeric relationship holding between the "
                    "answers to two different questions?"
                ),
                "focus": (
                    "Expecting a question and its negation to sum to one. Comparing a Noul "
                    "value against a Choice option's probability. Using one threshold number "
                    "for two differently worded questions, or for two different primitive "
                    "types. Reconstructing a magnitude by interpolating between Score levels."
                ),
            },
            {
                "true": (
                    "The described policy would break if two separate questions' numbers were "
                    "on different scales or did not sum as expected."
                ),
                "false": (
                    "Each question's number is read on its own terms, with its own cutoff, and "
                    "no arithmetic identity between questions is assumed."
                ),
            },
        ),
        "mode8_duplicate_judgment": noul(
            {
                "question": (
                    "Do any two questions in `questions_asked` settle the same underlying "
                    "judgment?"
                ),
                "focus": (
                    "The same condition asked twice in two primitive types, or asked once "
                    "plainly and once as its negation."
                ),
            },
            {
                "true": (
                    "Two of the listed questions are the same judgment in different clothes."
                ),
                "false": "Each listed question settles a judgment no other one settles.",
            },
        ),
        # --- correlated evidence (not an upstream mode; see module docstring) --------------
        # Worded as a question about how the answers vary *across ordinary traffic*, not about
        # what writes each field. A shared-source wording was tried and rejected: it read 0.92
        # on two genuinely independent questions over one ticket body. See the tool's
        # REJECTED note.
        "correlated_evidence": noul(
            {
                "question": (
                    "In ordinary day-to-day operation, would the answers to two of the "
                    "questions in `questions_asked` almost always agree with each other?"
                ),
                "focus": (
                    "Two questions that read different parts of the state, but whose answers "
                    "move together because one underlying event produces both."
                ),
            },
            {
                "true": (
                    "Two of the listed questions would agree on nearly every input that "
                    "ordinary traffic produces."
                ),
                "false": "Each listed question's answer varies for its own reasons.",
            },
        ),
        # --- unbanded cutoff (not an upstream mode; see module docstring) ------------------
        "unbanded_cutoff": noul(
            {
                "question": (
                    "Does `code_policy` turn a probability straight into an action at a "
                    "single cutoff, with no middle range that hands the case to a person?"
                ),
                "focus": (
                    "A policy of the shape 'above this number do the thing, otherwise do "
                    "nothing'. Two inputs landing either side of that number get opposite "
                    "treatment and nothing sits between them. The alternative shape names two "
                    "numbers: below the lower one nothing happens, between them the case is "
                    "held for a person, above the higher one code acts."
                ),
            },
            {
                "true": (
                    "Each described decision is one comparison against one number, so a case "
                    "just below the number and a case just above it get opposite treatment "
                    "with no review step between them."
                ),
                "false": (
                    "The policy names a range between two numbers where the case is held for "
                    "a person to decide instead of being acted on automatically."
                ),
            },
        ),
        # --- consequence ------------------------------------------------------------------
        "consequence": score(
            {
                "question": (
                    "Suppose the questions in `questions_asked` come back wrong and the system "
                    "acts on them exactly as `code_policy` describes. Where does the damage "
                    "land?"
                ),
                "focus": (
                    "Judge by what the code does with the answers, not by how likely a wrong "
                    "answer is."
                ),
            },
            CONSEQUENCE_LEVELS,
        ),
    },
))
