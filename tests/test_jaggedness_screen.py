"""Live tests for tools/jaggedness_screen.py.

Run: python3 tests/test_jaggedness_screen.py
Requires TYPESAFE_API_KEY in the environment.

HOW THESE ASSERT. No test here compares a probability against a number someone chose. No
cutoff over these numbers has been measured, so asserting `exposure > 0.7` would be asserting
an invented threshold -- the exact thing AGENTS.md rule 7 forbids.

Instead every live assertion is **paired and comparative**. Each probe design has a twin that
differs in exactly one thing: the counting question has a semantic twin over the identical
state; the date-comparison question has a date-extraction twin; the padded state has a lean
twin carrying the same question; the crossed-polarity Noul has the same judgment worded
straight. The assertion is that the mode's exposure is strictly higher on the tripping design
than on its twin. That is falsifiable, it survives recalibration, and it tests the mechanism
the tool actually sells -- separating a design that trips a mode from one that does not.

Seven of the nine modes are covered by a live pair (2, 3, 4, 5, 7, 8, 9). Mode 6 is covered by
its mechanism instead, because provenance is declared input rather than a measured property.

The three model-judged local checks -- `attacker_premise`, `correlated_evidence`,
`unbanded_cutoff` -- are held to the same standard and get live pairs of their own, plus the
controls that would catch them over-firing: a question reading the same foreign field for a
dashboard badge (exposure must hold, consequence must fall) and a Choice-argmax policy that
compares no probability to anything (`unbanded_cutoff` must stay quiet). The fourth,
`unmeasured_threshold`, is decided by `re` and a dict lookup, so it is asserted exactly.
Mode 1 has NO pair here on purpose: measured, its exposure Noul floors around 0.80 on every
question tried, clean ones included, so a pair would assert a separation the question does not
actually deliver. See the LIMITATION note in tools/jaggedness_screen.py.

The non-comparative assertions are pure mechanism and need no network: the unknown-provenance
path, the remedy table, the severity arithmetic, the loader's refusals.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "lib"))

from jaggedness_screen import (  # noqa: E402
    CHECKS,
    CLASS_WEIGHT,
    EXTRA_CHECKS,
    MODES,
    REMEDY,
    Finding,
    ScreenReport,
    load_design,
    screen,
)
from typesafe_client import TypeSafeClient  # noqa: E402

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


def expect_raises(name, exc_type, fn):
    global passed, failed
    try:
        fn()
    except exc_type as exc:
        passed += 1
        print(f"  PASS  {name} -- {type(exc).__name__}: {str(exc)[:60]}")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"  FAIL  {name} -- raised {type(exc).__name__}, wanted {exc_type.__name__}")
    else:
        failed += 1
        print(f"  FAIL  {name} -- nothing raised")


# --- Probe designs -------------------------------------------------------------------------
#
# Each pair differs in exactly one dimension. Provenance is pinned to internal/handled on the
# pairs that are not about mode 6, so mode 6 cannot contaminate the comparison.

SAFE_PROVENANCE = {"state_source": "internal", "downstream_untrusted_handling": True}

INBOX_STATE = (
    "`inbox` is a list of 40 to 120 support ticket bodies for one account, each 50-400 words, "
    "written by customers."
)

COUNTING = {
    "name": "probe-counting",
    "purpose": "Decide whether a support backlog is refund-heavy.",
    "state_description": INBOX_STATE,
    "code_policy": "The answer picks which of four backlog playbooks runs.",
    "questions": [{
        "id": "refund_tally",
        "type": "score",
        "instructions": "How many of the tickets in `inbox` are asking for a refund?",
        "criteria": ["None of them", "Under a quarter of them", "Most of them", "All of them"],
        "used_for": "Selects the backlog playbook.",
    }],
    **SAFE_PROVENANCE,
}

COUNTING_TWIN = {
    "name": "probe-counting-twin",
    "purpose": "Decide whether a support backlog is refund-heavy.",
    "state_description": INBOX_STATE,
    "code_policy": "The answer picks which of four backlog playbooks runs.",
    "questions": [{
        "id": "refund_flavour",
        "type": "score",
        "instructions": "How much of the frustration expressed across `inbox` is about money "
                        "the customer wants back, rather than about a product not working?",
        "criteria": ["Entirely about the product not working",
                     "Mostly about the product, with money mentioned in passing",
                     "Money and product complaints carry equal weight",
                     "Entirely about getting money back"],
        "used_for": "Selects the backlog playbook.",
    }],
    **SAFE_PROVENANCE,
}

INVOICE_STATE = (
    "`invoice` has fields `issued_on`, `due_on` and `terms`, each a date or short phrase as "
    "typed by the vendor. `payment` has `received_at`."
)

DATE_COMPARE = {
    "name": "probe-date-compare",
    "purpose": "Decide whether an invoice was paid late.",
    "state_description": INVOICE_STATE,
    "code_policy": "A late invoice moves the vendor to the watchlist.",
    "questions": [{
        "id": "paid_late",
        "type": "noul",
        "instructions": "Did `payment.received_at` fall after `invoice.due_on`, counting only "
                        "business days and treating the quarter boundary as a hard cutoff?",
        "criteria": {"true": "The payment arrived after the due date.",
                     "false": "The payment arrived on or before the due date."},
        "used_for": "Moves the vendor to the watchlist.",
    }],
    **SAFE_PROVENANCE,
}

DATE_EXTRACT_TWIN = {
    "name": "probe-date-extract-twin",
    "purpose": "Decide whether an invoice was paid late.",
    "state_description": INVOICE_STATE,
    "code_policy": "Code assembles the extracted parts into dates and compares them itself.",
    "questions": [{
        "id": "due_month",
        "type": "choice",
        "instructions": "Which month does `invoice.due_on` name?",
        "criteria": {m: None for m in
                     ["january", "february", "march", "april", "may", "june", "july",
                      "august", "september", "october", "november", "december",
                      "not_stated"]},
        "used_for": "Code assembles the date and does the comparison.",
    }],
    **SAFE_PROVENANCE,
}

POLITENESS_Q = {
    "id": "is_polite",
    "type": "noul",
    "instructions": "Is `message.body` written politely?",
    "criteria": {"true": "It is courteous in tone.",
                 "false": "It is curt, sarcastic, or hostile."},
    "used_for": "Tags the message for a human reviewer to read.",
}

PADDED = {
    "name": "probe-padded-state",
    "purpose": "Tag inbound messages by tone.",
    "state_description": (
        "`message.body`, plus the sender's full CRM profile, their last 90 days of product "
        "event history, every previous message in the thread, the account's billing history, "
        "the full list of files in the linked support repository, and the team's on-call "
        "rota for the quarter."
    ),
    "code_policy": "The tag is shown next to the message in a review queue.",
    "questions": [POLITENESS_Q],
    **SAFE_PROVENANCE,
}

LEAN_TWIN = {
    "name": "probe-lean-state-twin",
    "purpose": "Tag inbound messages by tone.",
    "state_description": "`message.body`, the text of one message, and nothing else.",
    "code_policy": "The tag is shown next to the message in a review queue.",
    "questions": [POLITENESS_Q],
    **SAFE_PROVENANCE,
}

GENERATION = {
    "name": "probe-generation",
    "purpose": "Label an incident.",
    "state_description": "`incident.report`, a paragraph written by the on-call engineer.",
    "code_policy": "The produced label becomes the incident title in the tracker.",
    "questions": [{
        "id": "title",
        "type": "choice",
        "instructions": "Write a short title naming the root cause described in "
                        "`incident.report`.",
        "criteria": "(the model supplies the wording)",
        "used_for": "Becomes the incident title.",
    }],
    **SAFE_PROVENANCE,
}

SELECTION_TWIN = {
    "name": "probe-selection-twin",
    "purpose": "Label an incident.",
    "state_description": "`incident.report`, a paragraph written by the on-call engineer.",
    "code_policy": "The chosen label becomes the incident category in the tracker.",
    "questions": [{
        "id": "root_cause",
        "type": "choice",
        "instructions": "Which root cause does `incident.report` describe?",
        "criteria": {"out_of_memory": "A process was killed for exceeding its memory limit",
                     "bad_deploy": "A change shipped and broke behaviour that worked before",
                     "upstream_outage": "A dependency outside this system stopped responding",
                     "other": "None of the above fits"},
        "used_for": "Becomes the incident category.",
    }],
    **SAFE_PROVENANCE,
}

TICKET_STATE = "`ticket`, the body of one support ticket."

MULTI_HOP = {
    "name": "probe-indirection",
    "purpose": "Flag tickets with a near deadline.",
    "state_description": TICKET_STATE,
    "code_policy": "A flagged ticket is shown first in the review queue.",
    "questions": [{
        "id": "urgent_soon",
        "type": "noul",
        "instructions": "Does `ticket` mention a deadline that is not more than a week away, "
                        "unless the sender says it has already passed?",
        "criteria": "(none given)",
        "used_for": "Orders the review queue.",
    }],
    **SAFE_PROVENANCE,
}

ONE_HOP_TWIN = {
    "name": "probe-indirection-twin",
    "purpose": "Flag tickets by language.",
    "state_description": TICKET_STATE,
    "code_policy": "A flagged ticket is shown first in the review queue.",
    "questions": [{
        "id": "in_english",
        "type": "noul",
        "instructions": "Is `ticket` written in English?",
        "criteria": {"true": "The body is written in English throughout.",
                     "false": "The body is wholly or partly in another language."},
        "used_for": "Orders the review queue.",
    }],
    **SAFE_PROVENANCE,
}

# A Noul whose `true` description is the answer a reader would give to "no" -- the exact
# shape the doc names: "a Noul where `true` maps to no and `false` maps to yes".
CROSSED_POLARITY = {
    "name": "probe-crossed-polarity",
    "purpose": "Escalate open tickets.",
    "state_description": TICKET_STATE,
    "code_policy": "Above a cutoff the ticket is escalated.",
    "questions": [{
        "id": "not_resolved",
        "type": "noul",
        "instructions": "Is `ticket` already resolved?",
        "criteria": {"true": "The ticket is still open and needs work.",
                     "false": "The ticket has been dealt with and needs nothing further."},
        "used_for": "Above a cutoff the ticket is escalated.",
    }],
    **SAFE_PROVENANCE,
}

ALIGNED_TWIN = {
    "name": "probe-aligned-polarity-twin",
    "purpose": "Escalate open tickets.",
    "state_description": TICKET_STATE,
    "code_policy": "Above a cutoff the ticket is escalated.",
    "questions": [{
        "id": "still_open",
        "type": "noul",
        "instructions": "Is `ticket` still open and in need of work?",
        "criteria": {"true": "The ticket is still open and needs work.",
                     "false": "The ticket has been dealt with and needs nothing further."},
        "used_for": "Above a cutoff the ticket is escalated.",
    }],
    **SAFE_PROVENANCE,
}

SPAM_NOUL = {
    "id": "is_spam", "type": "noul", "instructions": "Is `ticket` spam?",
    "criteria": {"true": "It is unsolicited bulk mail.",
                 "false": "It is a genuine customer message."},
    "used_for": "Files the ticket.",
}
SPAM_CHOICE = {
    "id": "spam_choice", "type": "choice", "instructions": "Is `ticket` spam?",
    "criteria": {"yes": "It is spam", "no": "It is genuine"},
    "used_for": "Files the ticket.",
}

INVARIANT_RELIANT = {
    "name": "probe-invariants",
    "purpose": "File tickets.",
    "state_description": TICKET_STATE,
    "code_policy": "We check that is_spam and one minus the Choice's `no` probability agree "
                   "within 0.05, and apply the same 0.8 cutoff to both.",
    "questions": [SPAM_NOUL, SPAM_CHOICE],
    **SAFE_PROVENANCE,
}

INVARIANT_CLEAN_TWIN = {
    "name": "probe-invariants-twin",
    "purpose": "File tickets.",
    "state_description": TICKET_STATE,
    "code_policy": "is_spam is read against its own cutoff, measured on a labelled set. No "
                   "other question feeds this decision.",
    "questions": [SPAM_NOUL],
    **SAFE_PROVENANCE,
}

# --- the three locally-measured checks, each with a one-variable twin ----------------------
#
# These are not upstream modes, so they get the same treatment the upstream ones get: a
# deliberately-bad design against a twin differing in exactly one thing, asserted comparatively.

REGISTRY_STATE = (
    "`registry_entry`, our own record of the model. `probe.status_history`, the list of HTTP "
    "status codes our own prober observed on the last ten calls to the endpoint. "
    "`provider_message`, the verbatim response body text returned by the third-party "
    "provider's API and written by that provider."
)
REGISTRY_DESIGN = {
    "name": "probe-attacker-premise",
    "purpose": "Decide whether a model entry should be removed from the registry.",
    "state_description": REGISTRY_STATE,
    "code_policy": "Above its own measured cutoff the registry row is deleted.",
    **SAFE_PROVENANCE,
}
Q_READS_PROVIDER = {
    "id": "withdrawal_announced", "type": "noul",
    "instructions": "Does `provider_message` announce that this model has been withdrawn from "
                    "service?",
    "criteria": {"true": "The message says the model is retired, withdrawn, or no longer "
                         "available.",
                 "false": "The message says nothing about the model being withdrawn."},
    "used_for": "Above the cutoff the registry row is deleted.",
}
Q_READS_OUR_PROBE = {
    "id": "probe_consistently_failing", "type": "noul",
    "instructions": "Do the codes in `probe.status_history`, recorded by our own prober, show "
                    "the endpoint failing on every one of the last ten calls?",
    "criteria": {"true": "Every recorded status code is a failure.",
                 "false": "At least one recorded status code is a success."},
    "used_for": "Above the cutoff the registry row is deleted.",
}
ATTACKER_PREMISE = {**REGISTRY_DESIGN, "questions": [Q_READS_PROVIDER]}
# One variable: which field the gating question reads. The provider-written field stays in the
# state description in both, so this cannot be passing by noticing foreign text is present.
OWN_OBSERVATION_TWIN = {**REGISTRY_DESIGN, "name": "probe-attacker-premise-twin",
                        "questions": [Q_READS_OUR_PROBE]}
# Same question, same foreign field, but the answer only paints a badge. Exposure should hold
# while consequence falls -- the two are meant to be orthogonal.
ATTACKER_PREMISE_HARMLESS = {
    **REGISTRY_DESIGN, "name": "probe-attacker-premise-display",
    "code_policy": "The answer is shown as a badge in an internal dashboard.",
    "questions": [{**Q_READS_PROVIDER,
                   "used_for": "Shown as a badge next to the entry in an internal dashboard. "
                               "Nothing is changed automatically."}],
}

Q_STATUS_GONE = {
    "id": "status_says_gone", "type": "noul",
    "instructions": "Do the codes in `probe.status_history` show the endpoint returning "
                    "not-found on every call?",
    "criteria": {"true": "Every recorded status is a not-found.",
                 "false": "At least one recorded status is not a not-found."},
    "used_for": "Feeds the removal decision.",
}
Q_MESSAGE_GONE = {
    "id": "message_says_gone", "type": "noul",
    "instructions": "Does `provider_message` say the model is no longer available?",
    "criteria": {"true": "The message says the model is gone.",
                 "false": "The message says nothing about the model being gone."},
    "used_for": "Feeds the removal decision.",
}
Q_OUR_TIER = {
    "id": "entry_is_experimental", "type": "noul",
    "instructions": "Does `registry_entry.tier` read experimental?",
    "criteria": {"true": "The tier our team typed is experimental.",
                 "false": "The tier our team typed is standard or pinned."},
    "used_for": "Feeds the removal decision.",
}
CORRELATED = {
    "name": "probe-correlated-evidence",
    "purpose": "Decide whether a model entry should be removed from the registry.",
    "state_description": REGISTRY_STATE + " `registry_entry.tier` is a label our own team typed "
                                         "when the entry was created.",
    "code_policy": "Both questions must clear their own measured cutoffs before the row is "
                   "deleted.",
    "questions": [Q_STATUS_GONE, Q_MESSAGE_GONE],
    **SAFE_PROVENANCE,
}
# One variable: the second signal. A tier our team typed has no reason to move with the
# provider's status, so ordinary traffic produces inputs where the two disagree.
INDEPENDENT_TWIN = {**CORRELATED, "name": "probe-correlated-evidence-twin",
                    "questions": [Q_STATUS_GONE, Q_OUR_TIER]}

SCAM_Q = {
    "id": "is_scam", "type": "noul", "instructions": "Is `listing.text` a scam?",
    "criteria": {"true": "It is a scam.", "false": "It is a genuine listing."},
    "used_for": "Drives the takedown decision.",
}
UNBANDED = {
    "name": "probe-unbanded-cutoff",
    "purpose": "Take down scam marketplace listings.",
    "state_description": "`listing.text`, the body of a marketplace listing written by a "
                         "seller.",
    "code_policy": "If `is_scam` is above 0.5 the listing is deleted and the seller is "
                   "suspended. Otherwise nothing happens.",
    "questions": [SCAM_Q],
    "state_source": "user_input", "downstream_untrusted_handling": True,
}
# One variable: the shape of the policy. Same question, same state, same action.
BANDED_TWIN = {
    **UNBANDED, "name": "probe-banded-cutoff-twin",
    "code_policy": "Below 0.30 nothing happens. Between 0.30 and 0.85 the listing is queued "
                   "for a human moderator to decide. Above 0.85 the listing is deleted and "
                   "the seller is suspended.",
}
# A design that compares no probability to anything. If `unbanded_cutoff` fired here it would
# fire on every Choice-driven design ever screened.
ARGMAX_CONTROL = {
    "name": "probe-argmax-no-cutoff",
    "purpose": "Route a ticket to a queue.",
    "state_description": "`ticket.body`, one support ticket.",
    "code_policy": "The Choice's winning option names the queue the ticket is filed into. No "
                   "probability is compared against any number.",
    "questions": [{"id": "queue", "type": "choice",
                   "instructions": "Which queue should `ticket.body` go to?",
                   "criteria": {"billing": "About money", "technical": "About a fault",
                                "other": "Neither fits"},
                   "used_for": "Names the queue."}],
    "state_source": "user_input", "downstream_untrusted_handling": True,
}

UNDECLARED = {
    "name": "probe-undeclared-provenance",
    "purpose": "Decide whether a scraped listing is a scam.",
    "state_description": "`listing.text`, the body of a marketplace listing.",
    "code_policy": "A listing judged a scam is deleted and the seller is suspended.",
    "questions": [{
        "id": "is_scam",
        "type": "noul",
        "instructions": "Is `listing.text` a scam?",
        "criteria": {"true": "It is a scam.", "false": "It is a genuine listing."},
        "used_for": "Deletes the listing and suspends the seller.",
    }],
    # state_source and downstream_untrusted_handling deliberately absent.
}


# --- [1] Mechanism, no network -------------------------------------------------------------

print("\n[1] Mechanism (no network)")

check("all nine modes named", sorted(MODES) == list(range(1, 10)), f"{sorted(MODES)}")
check("every check has a remedy", sorted(REMEDY, key=str) == sorted(CHECKS, key=str))
check("every check has a class weight", sorted(CLASS_WEIGHT, key=str) == sorted(CHECKS, key=str))
check("remedies are non-trivial", all(len(r) > 80 for r in REMEDY.values()),
      f"shortest {min(len(r) for r in REMEDY.values())} chars")
check("the four local checks carry string ids, never numbers",
      all(isinstance(k, str) for k in EXTRA_CHECKS) and len(EXTRA_CHECKS) == 4,
      f"{sorted(EXTRA_CHECKS)}")
check("CHECKS is the union and the two do not collide",
      len(CHECKS) == len(MODES) + len(EXTRA_CHECKS))

expect_raises("loader refuses a design with no questions", ValueError,
              lambda: load_design({"name": "x", "questions": []}))
expect_raises("loader refuses a question with no instructions", ValueError,
              lambda: load_design({"name": "x", "questions": [{"id": "a"}]}))

undeclared = load_design(UNDECLARED)
check("undeclared provenance is recorded, not defaulted",
      undeclared.state_source == "unknown" and len(undeclared.unknowns) == 2,
      f"{len(undeclared.unknowns)} unknown(s)")
check("undeclared provenance is not treated as untrusted-by-guess",
      undeclared.state_is_untrusted is False)
check("bogus provenance value falls back to unknown with a note",
      load_design({**UNDECLARED, "state_source": "probably fine",
                   "downstream_untrusted_handling": True}).state_source == "unknown")

# severity is pure arithmetic over three reported components; an unscreenable mode takes
# worst-case exposure so it cannot sort below a screened one of equal consequence.
screened = Finding(6, MODES[6], 0.40, 1.0, CLASS_WEIGHT[6], "", "", "")
unknown = Finding(6, MODES[6], None, 1.0, CLASS_WEIGHT[6], "", "", "", status="unknown")
check("severity = weight x exposure x consequence",
      abs(screened.severity - 0.9 * 0.40 * 1.0) < 1e-9, f"{screened.severity:.4f}")
check("an unscreenable mode ranks at worst case, not zero",
      unknown.severity > screened.severity, f"{unknown.severity:.2f} > {screened.severity:.2f}")

fake = ScreenReport(undeclared, [screened, Finding(2, MODES[2], 0.9, 1.0, 1.0, "", "", "")],
                    0, 0, 0.0, 0, "jev-1.13.0")
check("ranked() orders by severity", fake.top_mode() == 2, f"top={fake.top_mode()}")
check("complete is False while an unknown stands", fake.complete is False)

# Mixed int/str ids are not orderable against each other, so ranked() must separate them
# before comparing. Three findings at exactly equal severity is the case that would raise.
tied = ScreenReport(
    undeclared,
    [Finding("unbanded_cutoff", "Unbanded cutoff", 0.5, 1.0, 1.0, "", "", ""),
     Finding(4, MODES[4], 0.5, 1.0, 1.0, "", "", ""),
     Finding("attacker_premise", "Attacker-settable premise", 0.5, 1.0, 1.0, "", "", "")],
    0, 0, 0.0, 0, "jev-1.13.0")
check("ranked() survives an exact severity tie across int and str ids",
      [f.mode for f in tied.ranked()] == [4, "attacker_premise", "unbanded_cutoff"],
      f"{[f.mode for f in tied.ranked()]}")
check("upstream_findings() excludes the locally-measured checks",
      [f.mode for f in tied.upstream_findings()] == [4])
check("is_upstream_mode separates the two registries",
      tied.finding(4).is_upstream_mode is True
      and tied.finding("attacker_premise").is_upstream_mode is False)
expect_raises("finding() refuses an id it does not carry", KeyError,
              lambda: tied.finding("no_such_check"))
check("to_dict marks each row upstream or not",
      all("upstream_mode" in row for row in tied.to_dict()["findings"]))

# `unmeasured_threshold` is decided by `re` and a dict lookup, with no API call, so it is
# asserted exactly rather than comparatively. It is not a measurement and is not treated as
# one -- see the tool's docstring.
from jaggedness_screen import _unmeasured_threshold_finding  # noqa: E402

def _policy(text, validated=None):
    d = load_design({**UNDECLARED, "code_policy": text,
                     **({"thresholds_validated_on": validated} if validated else {})})
    return _unmeasured_threshold_finding(d, 1.0)

check("a bare cutoff with no declared provenance is exposed",
      _policy("If is_scam is above 0.5 the listing is deleted.").exposure == 1.0)
check("the same cutoff with declared provenance is not",
      _policy("If is_scam is above 0.5 the listing is deleted.",
              "200 labelled listings, 15 runs each").exposure == 0.0)
check("a policy naming no number is not exposed",
      _policy("The Choice's winning option names the queue.").exposure == 0.0)
check("the finding names the number it matched",
      "0.5" in _policy("If is_scam is above 0.5 the listing is deleted.").evidence)
check("the deterministic finding says it made no API call",
      "no API call" in _policy("above 0.5 delete").evidence)


# --- Live pairs ----------------------------------------------------------------------------

client = TypeSafeClient()
reports: dict[str, ScreenReport] = {}


def run(design: dict) -> ScreenReport:
    report = screen(load_design(design), client=client)
    reports[design["name"]] = report
    return report


def pair(name, mode, tripping, clean):
    """Assert the mode separates the design that trips it from its one-variable twin."""
    hi, lo = tripping.exposure(mode), clean.exposure(mode)
    check(name, hi > lo,
          f"mode {mode}: {tripping.design.name}={hi:.2f} vs {clean.design.name}={lo:.2f}")


print("\n[2] Mode 2 -- counting vs the same state judged semantically")
c_hi, c_lo = run(COUNTING), run(COUNTING_TWIN)
pair("counting question is more exposed to mode 2", 2, c_hi, c_lo)
check("mode 2 is the top-ranked mode for the counting design", c_hi.top_mode() == 2,
      f"top={c_hi.top_mode()} ({MODES[c_hi.top_mode()]}), severity={c_hi.finding(2).severity:.2f}")
check("mode 2 names the offending question", "refund_tally" in c_hi.finding(2).where,
      c_hi.finding(2).where)

print("\n[3] Mode 3 -- date comparison vs date-part extraction")
d_hi, d_lo = run(DATE_COMPARE), run(DATE_EXTRACT_TWIN)
pair("date-comparison question is more exposed to mode 3", 3, d_hi, d_lo)
check("mode 3 is the top-ranked mode for the comparison design", d_hi.top_mode() == 3,
      f"top={d_hi.top_mode()} ({MODES[d_hi.top_mode()]})")

print("\n[4] Mode 5 -- padded state vs lean state, identical question")
p_hi, p_lo = run(PADDED), run(LEAN_TWIN)
pair("padded state is more exposed to mode 5", 5, p_hi, p_lo)
check("mode 5 points at the state, not a question",
      p_hi.finding(5).where == "the state description", p_hi.finding(5).where)
check("the shared question scores alike on mode 1 in both",
      abs(p_hi.exposure(1) - p_lo.exposure(1)) < 0.25,
      f"padded={p_hi.exposure(1):.2f} lean={p_lo.exposure(1):.2f} "
      "(same question text; mode 5 moved, mode 1 should not)")

print("\n[5] Mode 4 -- a question with a stacked exception vs a one-hop question")
i_hi, i_lo = run(MULTI_HOP), run(ONE_HOP_TWIN)
pair("the multi-hop question is more exposed to mode 4", 4, i_hi, i_lo)

print("\n[6] Mode 7 -- a Noul whose `true` reads as 'no' vs the same judgment worded straight")
t_hi, t_lo = run(CROSSED_POLARITY), run(ALIGNED_TWIN)
pair("crossed polarity is more exposed to mode 7", 7, t_hi, t_lo)
check("mode 7 names the crossed question", "not_resolved" in t_hi.finding(7).where,
      t_hi.finding(7).where)

print("\n[7] Mode 8 -- policy that leans on an identity vs one that does not")
v_hi, v_lo = run(INVARIANT_RELIANT), run(INVARIANT_CLEAN_TWIN)
pair("the identity-reliant policy is more exposed to mode 8", 8, v_hi, v_lo)
check("mode 8 points at code_policy", v_hi.finding(8).where in ("code_policy", "the question set"),
      v_hi.finding(8).where)
check("mode 8 reports both halves separately",
      "policy_relies_on_identity" in v_hi.finding(8).evidence
      and "duplicate_judgment" in v_hi.finding(8).evidence, v_hi.finding(8).evidence)

print("\n[8] Mode 9 -- generate a title vs select a category")
g_hi, g_lo = run(GENERATION), run(SELECTION_TWIN)
pair("the generating question is more exposed to mode 9", 9, g_hi, g_lo)

print("\n[9] Mode 6 -- the untrusted-state question is never silently passed")
u = run(UNDECLARED)
u6 = u.finding(6)
check("mode 6 is reported UNKNOWN when provenance is undeclared", u6.status == "unknown",
      f"status={u6.status}")
check("mode 6 exposure is None, not a number the caller could misread", u6.exposure is None)
check("the report is incomplete", u.complete is False)
check("the unknown names the missing field", "state_source" in u6.where, u6.where)
check("the remedy tells the designer to assume untrusted",
      "assume untrusted" in u6.remedy.lower())
check("mode 6 sorts to the top when unscreenable and the action is destructive",
      u.top_mode() == 6, f"top={u.top_mode()} ({MODES[u.top_mode()]})")

declared = run({**UNDECLARED, "name": "probe-declared-untrusted",
                "state_source": "scraped", "downstream_untrusted_handling": False})
d6 = declared.finding(6)
check("declaring the provenance makes mode 6 screenable",
      d6.status == "screened" and d6.exposure is not None, f"exposure={d6.exposure:.2f}")
check("missing downstream handling is itself reported",
      "not a security boundary" in d6.evidence)
check("mode 6 reports both measured halves separately",
      "model_reads_state_as_foreign" in d6.evidence
      and "criteria_declare_untrusted" in d6.evidence, d6.evidence[:90])

print("\n[10] Attacker-settable premise -- a correct answer an outsider gets to set")
a_hi, a_lo = run(ATTACKER_PREMISE), run(OWN_OBSERVATION_TWIN)
pair("the question reading the provider's text is more exposed", "attacker_premise", a_hi, a_lo)
check("the finding names the question and what it gates",
      "withdrawal_announced" in a_hi.finding("attacker_premise").where
      and "deleted" in a_hi.finding("attacker_premise").where,
      a_hi.finding("attacker_premise").where)
check("it is reported as a local check, not an upstream mode",
      a_hi.finding("attacker_premise").is_upstream_mode is False)
a_display = run(ATTACKER_PREMISE_HARMLESS)
check("exposure and consequence stay orthogonal",
      abs(a_display.exposure("attacker_premise") - a_hi.exposure("attacker_premise")) < 0.20
      and a_display.finding("attacker_premise").consequence
      < a_hi.finding("attacker_premise").consequence,
      f"display exposure={a_display.exposure('attacker_premise'):.2f} "
      f"consequence={a_display.finding('attacker_premise').consequence:.2f} vs gating "
      f"consequence={a_hi.finding('attacker_premise').consequence:.2f}")
check("severity ranks the gating design above the display one",
      a_hi.finding("attacker_premise").severity
      > a_display.finding("attacker_premise").severity,
      f"{a_hi.finding('attacker_premise').severity:.2f} > "
      f"{a_display.finding('attacker_premise').severity:.2f}")

print("\n[11] Correlated evidence -- two signals that travel together vs two that do not")
e_hi, e_lo = run(CORRELATED), run(INDEPENDENT_TWIN)
pair("signals that move together are more exposed", "correlated_evidence", e_hi, e_lo)
check("mode 8 does not stand in for it -- these are different judgments",
      e_hi.exposure(8) < e_hi.exposure("correlated_evidence"),
      f"duplicate-judgment={e_hi.exposure(8):.2f} vs "
      f"correlated={e_hi.exposure('correlated_evidence'):.2f} (genuinely different questions "
      "whose answers travel together)")

print("\n[12] Unbanded cutoff -- one number vs a band that holds the middle for a person")
b_hi, b_lo = run(UNBANDED), run(BANDED_TWIN)
pair("a single cutoff is more exposed", "unbanded_cutoff", b_hi, b_lo)
argmax = run(ARGMAX_CONTROL)
check("a design that compares no probability to anything is not flagged",
      argmax.exposure("unbanded_cutoff") < b_hi.exposure("unbanded_cutoff"),
      f"argmax={argmax.exposure('unbanded_cutoff'):.2f} vs "
      f"single-cutoff={b_hi.exposure('unbanded_cutoff'):.2f}")
check("the unbanded design also trips the deterministic threshold check",
      b_hi.exposure("unmeasured_threshold") == 1.0,
      b_hi.finding("unmeasured_threshold").evidence)
check("declaring the provenance clears it without another API call",
      run({**UNBANDED, "name": "probe-unbanded-declared",
           "thresholds_validated_on": "600 labelled listings, 15 runs each"})
      .exposure("unmeasured_threshold") == 0.0)

print("\n[13] Cost")
total_req = sum(r.requests for r in reports.values())
total_usd = sum(r.cost_usd for r in reports.values())
total_ms = sum(r.latency_ms for r in reports.values())
check("one request per question plus one per design",
      all(r.requests == len(r.design.questions) + 1 for r in reports.values()),
      f"{total_req} requests across {len(reports)} designs")
check("model stayed pinned", all(r.model == "jev-1.13.0" for r in reports.values()),
      {r.model for r in reports.values()})
print(f"        {total_req} requests | ${total_usd:.6f} | {total_ms} ms")

print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
