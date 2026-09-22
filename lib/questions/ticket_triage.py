"""Demo/measurement QuestionSet: support-ticket triage.

Not tied to any production surface. Registered so `tools/consistency_probe.py` (and anyone
else exercising `lib/questions/`) has something real to point at, rather than inventing
throwaway criteria per run. The battery is the one already proven live in
`tests/test_client.py`'s smoke test -- reused rather than redesigned, so any instability a
probe finds here is signal about the model, not about a freshly written question.

`gates_irreversible=False` and `validated_on=None` are deliberate: nothing acts on these
answers yet, so there is nothing to validate accuracy against. If that changes,
route through typesafe-calibrator for an accuracy pass before flipping `gates_irreversible`
-- self-consistency (what `consistency_probe.py` measures) is a different question from
correctness and does not substitute for it.
"""

from . import QuestionSet, register
from typesafe_client import choice, noul, score

TICKET_TRIAGE = QuestionSet(
    name="ticket_triage",
    version="1.0.0",
    description="Route + score a support ticket: team, severity, report quality, repeat "
                 "contact, refund request.",
    questions={
        "team": choice(
            {"question": "Which team should handle `ticket`?",
             "focus": "Classify the primary request."},
            {"engineering": {"what": "Bugs, outages, broken features",
                             "not_for": "Charges or account access",
                             "examples": ["export button does nothing"]},
             "billing": {"what": "Charges, invoices, refunds",
                         "not_for": "Broken features",
                         "examples": ["I was charged twice"]},
             "other": None},
        ),
        "severity": score(
            "How severe is the issue reported in `ticket`?",
            ["Cosmetic; no impact to functionality",
             "Broken or degraded feature, but workaround exists",
             "Blocking issue; no workaround exists"],
        ),
        "report_quality": score(
            "How much does `ticket` give an engineer to work with?",
            ["No detail; just says something is broken",
             "Names the feature but no steps or environment",
             "Steps to reproduce or environment, but not both",
             "Steps to reproduce and environment"],
        ),
        "repeat_contact": noul(
            "Has the customer contacted support about this before?",
            {"true": "Mentions a prior attempt or that they have written before",
             "false": "No sign of previous contact"},
        ),
        "refund_requested": noul("Does `ticket` explicitly request a refund or credit?"),
    },
    gates_irreversible=False,
    validated_on=None,
)

register(TICKET_TRIAGE)
