"""Live tests for tools/question_critic.py.

Run: python3 tests/test_question_critic.py

Every live assertion here is a **comparison between two drafts**, never a comparison against
a number someone picked. A cutoff invented in a test is exactly the failure `validated_on`
exists to prevent (see tests/test_questions.py, where an invented `noul > 0.9` failed at 0.76).
The critic's own bands are provisional for the same reason, so what is asserted is that the
critic *separates* a bad draft from a good one and that its mechanism holds -- not where the
line falls.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from question_critic import (  # noqa: E402
    CHECKS,
    CRITIC_SET,
    TAG,
    applicable_checks,
    critique,
    questions_for,
    structural_checks,
)
from typesafe_client import DEFAULT_USAGE_LOG, TypeSafeClient  # noqa: E402

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


# --- The drafts under review ----------------------------------------------------------------
#
# The bad ones are the mistakes agents actually make, not strawmen: degree-word levels, a
# bundled instruction, bare option names, and a Noul asked to carry an intensity.

BAD_SCORE = {
    "type": "score",
    "instructions": "Rate how urgent and important this ticket is, and whether the customer "
                    "is a paying customer.",
    "criteria": ["Not very urgent", "Somewhat urgent", "Very urgent", "Extremely urgent"],
}

GOOD_SCORE = {
    "type": "score",
    "instructions": "How severe is the problem reported in `ticket.body`? Judge only the "
                    "impact on the customer's ability to use the product, not how upset the "
                    "customer sounds.",
    "criteria": [
        "The product works as documented; the report concerns wording, layout, or a preference.",
        "A feature behaves incorrectly, and the customer can still reach the same outcome "
        "another way.",
        "A feature the customer needs is unusable and the report describes no workaround.",
        "The customer cannot use the product at all.",
    ],
}

BAD_CHOICE = {
    "type": "choice",
    "instructions": "Which category?",
    "criteria": {"billing": "Money stuff", "payments": "Payment stuff", "support": None},
}

GOOD_CHOICE = {
    "type": "choice",
    "instructions": "Which team should handle the message in `ticket.body`? Classify the "
                    "customer's primary request, not every topic the message mentions.",
    "criteria": {
        "returns": {
            "what": "Whether and how an item can be sent back, and what happens to the money "
                    "once it is",
            "not_for": "Where a package currently is, or when it will arrive",
            "examples": ["Can I return shoes I have worn once?"],
        },
        "shipping": {
            "what": "Where an order is now, when it will arrive, and packages that did not "
                    "arrive",
            "not_for": "Whether an item can be sent back once it has arrived",
            "examples": ["Tracking has said label created for two weeks."],
        },
        "other": {
            "what": "The message's primary request is not about returns or shipping",
            "not_for": "A message that does concern returns or shipping, even if it also "
                       "mentions something else",
            "examples": ["Please close my account."],
        },
    },
}

# A Noul asked to carry an intensity -- the documented misreading of `noul = 0.5`.
WRONG_PRIMITIVE = {
    "type": "noul",
    "instructions": "How strongly does the customer in `ticket.body` feel about the problem?",
}

GOOD_NOUL = {
    "type": "noul",
    "instructions": "Does the customer in `ticket.body` ask for money to be returned to them?",
    "criteria": {
        "true": "The message directly asks for a refund, a chargeback, or money back.",
        "false": "The message makes no request for money to be returned, even if it complains "
                 "about a charge.",
    },
}


# --- 1. Offline mechanism: which checks apply to which primitive ------------------------------

print("\n[1] Per-primitive question selection (no API call)")

score_only = {c.qid for c in CHECKS if c.applies_to == ("score",)}
choice_only = {c.qid for c in CHECKS if c.applies_to == ("choice",)}
noul_only = {c.qid for c in CHECKS if c.applies_to == ("noul",)}

for primitive, forbidden in (
    ("noul", score_only | choice_only),
    ("score", choice_only | noul_only),
    ("choice", score_only | noul_only),
):
    selected = set(questions_for(primitive))
    check(f"{primitive} draft receives no checks meant for other primitives",
          not (selected & forbidden), f"{len(selected)} checks, none of {sorted(forbidden)}")

check("score-only checks reach a score draft", score_only <= set(questions_for("score")),
      sorted(score_only))
check("criteria checks are dropped when a draft has no criteria",
      "criteria_conflicts" not in questions_for("noul", has_criteria=False)
      and "criteria_conflicts" in questions_for("noul", has_criteria=True))
expect_raises("unknown primitive refused", ValueError, lambda: questions_for("boolean"))
check("every check maps to a registered question",
      all(c.qid in CRITIC_SET.questions for c in CHECKS))

# A check whose evidence is absent must be reported as unasked, never scored as if the
# evidence were present and damning. This is the mechanism behind the `unstated_context`
# false positive: path resolution now lives in its own check, gated on having a state.
asked_bare, skipped_bare = applicable_checks("score", has_sample_state=False)
asked_state, skipped_state = applicable_checks("score", has_sample_state=True)
check("path resolution is not asked without a sample state",
      "dangling_state_path" in {c.qid for c in skipped_bare}
      and "dangling_state_path" not in {c.qid for c in asked_bare})
check("path resolution is asked once a sample state exists",
      "dangling_state_path" in {c.qid for c in asked_state} and skipped_state == [])
check("a skipped check explains why it was skipped",
      all(c.skipped_because.strip() for c in skipped_bare),
      skipped_bare[0].skipped_because[:56])
check("everything else is asked either way",
      {c.qid for c in asked_state} - {c.qid for c in asked_bare} == {"dangling_state_path"})


# --- 2. Offline mechanism: structural defects are settled in code -----------------------------

print("\n[2] Structural checks are deterministic, not judgments (no API call)")

bare = structural_checks({"type": "score", "instructions": "How bad is `x`?",
                          "criteria": ["0", "1", "2"]})
check("bare-number levels raise an error", any("bare numbers" in s.message for s in bare),
      "; ".join(s.message for s in bare))

too_many = structural_checks({"type": "score", "instructions": "`x`?",
                              "criteria": [f"situation {i}" for i in range(12)]})
check("out-of-range level count raises an error",
      any("2-10 levels" in s.message for s in too_many))

no_escape = structural_checks(BAD_CHOICE)
check("missing escape option is flagged",
      any("escape option" in s.message for s in no_escape))
check("missing backticked state path is flagged",
      any("backticked state path" in s.message for s in no_escape))

check("a well-formed draft trips no structural check",
      structural_checks(GOOD_CHOICE) == [], str(structural_checks(GOOD_CHOICE)))
check("an escape option is recognised in a good draft",
      not any("escape" in s.message for s in structural_checks(GOOD_CHOICE)))

bad_noul_criteria = structural_checks(
    {"type": "noul", "instructions": "Is `x` late?", "criteria": {"true": "a", "maybe": "b"}}
)
check("a Noul with non-true/false criteria raises an error",
      any("true/false" in s.message for s in bad_noul_criteria))


# --- 3. The tool gates on nothing -------------------------------------------------------------

print("\n[3] Advisory by construction")
check("critic set declares no thresholds", CRITIC_SET.thresholds == {},
      "nothing here is a measured cutoff")
check("critic set does not gate an irreversible action",
      CRITIC_SET.gates_irreversible is False)
check("critic set runs while unvalidated", not CRITIC_SET.is_validated)
check("model is pinned, not an alias", not CRITIC_SET.model.endswith("latest"),
      CRITIC_SET.model)


# --- 4. Live: the critic separates a bad Score from a good one --------------------------------

print("\n[4] Live -- bad Score vs good Score")
client = TypeSafeClient()
bad = critique(BAD_SCORE, client=client, name="bad_score",
               purpose="Sets the SLA timer for an incoming support ticket.")
good = critique(GOOD_SCORE, client=client, name="good_score",
                purpose="Sets the SLA timer for an incoming support ticket.")

check("both critiques returned findings", bool(bad.findings) and bool(good.findings),
      f"{len(bad.findings)} vs {len(good.findings)}")

# The flagship judgment: levels written as degrees vs levels written as situations.
check("degree-word levels score worse than situation levels",
      bad.signal("level_writing") > good.signal("level_writing"),
      f"bad={bad.signal('level_writing'):.2f} good={good.signal('level_writing'):.2f}")

check("a bundled instruction scores worse for hidden judgments",
      bad.signal("hidden_judgments") > good.signal("hidden_judgments"),
      f"bad={bad.signal('hidden_judgments'):.2f} good={good.signal('hidden_judgments'):.2f}")

check("the bad draft's worst finding outranks the good draft's worst finding",
      bad.worst.signal > good.worst.signal,
      f"bad {bad.worst.qid}={bad.worst.signal:.2f} > good {good.worst.qid}={good.worst.signal:.2f}")

check("the good draft trips no structural check", good.structural == [])
check("the bad draft is missing a backticked state path",
      any("backticked" in s.message for s in bad.structural))


# --- 5. Live: the critic separates a bad Choice from a good one -------------------------------

print("\n[5] Live -- bad Choice vs good Choice")
bad_c = critique(BAD_CHOICE, client=client, name="bad_choice",
                 purpose="Routes an inbound support ticket to a team.")
good_c = critique(GOOD_CHOICE, client=client, name="good_choice",
                  purpose="Routes an inbound support ticket to a team.")

for qid, label in (
    ("input_matches_no_option", "a missing escape option"),
    ("options_overlap", "overlapping options"),
    ("option_descriptions", "thin option descriptions"),
):
    check(f"{label} scores worse without contrastive criteria",
          bad_c.signal(qid) > good_c.signal(qid),
          f"bad={bad_c.signal(qid):.2f} good={good_c.signal(qid):.2f}")

check("the bad Choice's worst finding outranks the good Choice's",
      bad_c.worst.signal > good_c.worst.signal,
      f"bad {bad_c.worst.qid}={bad_c.worst.signal:.2f} > "
      f"good {good_c.worst.qid}={good_c.worst.signal:.2f}")


# --- 6. Live: the relative Choice and the absolute Noul agree on a mistyped draft -------------

print("\n[6] Live -- primitive mismatch, judged twice")
mistyped = critique(WRONG_PRIMITIVE, client=client, name="noul_for_intensity",
                    purpose="Decides how loudly to page the on-call engineer.")
right = critique(GOOD_NOUL, client=client, name="good_noul",
                 purpose="Decides whether to open a refund case.")

check("best-fit primitive names `score` for an intensity question",
      mistyped.best_primitive == "score",
      f"{mistyped.best_primitive} @ confidence {mistyped.best_primitive_confidence:.2f}")
check("best-fit primitive endorses a well-typed Noul",
      right.best_primitive == "noul",
      f"{right.best_primitive} @ confidence {right.best_primitive_confidence:.2f}")

# The Choice is relative and the Noul absolute, so the critic only calls a type wrong when
# both agree. Asserting they agree is asserting the composition, not a cutoff.
check("the absolute check agrees the mistyped draft is worse",
      mistyped.signal("declared_primitive_wrong") > right.signal("declared_primitive_wrong"),
      f"mistyped={mistyped.signal('declared_primitive_wrong'):.2f} "
      f"good={right.signal('declared_primitive_wrong'):.2f}")

mismatch = next(f for f in mistyped.findings if f.qid == "declared_primitive_wrong")
agreed = next(f for f in right.findings if f.qid == "declared_primitive_wrong")
check("a mistyped draft is told which type to use",
      "`score`" in (mismatch.detail or "") and "Change `type` to `score`" in mismatch.fix,
      mismatch.fix[:60])
check("a well-typed draft is told to leave its type alone",
      "Leave `type`" in agreed.fix, agreed.fix[:60])
check("a Noul draft receives the Noul-only phrasing check",
      any(f.qid == "high_means_absence" for f in right.findings))


# --- 6b. Live: a backticked path must not read as missing context ------------------------------
#
# Regression guard. `unstated_context` used to fire on *any* draft carrying a backticked path,
# because the critic's own state held no such path to resolve -- it was answering correctly
# about the harness, not about the draft. Path resolution is now its own check.

print("\n[6b] Live -- backticked paths are not missing context")

SAMPLE_STATE = {
    "ticket": {
        "body": "The export button does nothing on Safari. Works fine in Chrome, so I am "
                "using that for now.",
        "sender": "amy@example.com",
    }
}

# The doc-canonical severity question, which is known-good.
SEVERITY_CONTROL = {
    "type": "score",
    "instructions": "How severe is the issue reported in `ticket.body`?",
    "criteria": [
        "Cosmetic; no impact to functionality",
        "Broken or degraded feature, but workaround exists",
        "Blocking issue; no workaround exists",
    ],
}

# Genuinely missing context: defers to a policy document the draft does not contain.
MISSING_CONTEXT = {
    "type": "score",
    "instructions": "How severe is the issue in `ticket.body`, according to the severity "
                    "policy in our runbook?",
    "criteria": [
        "Below the P3 bar defined in the runbook",
        "Meets the P2 bar defined in the runbook",
        "Meets the P1 bar defined in the runbook",
    ],
}

# Reads two fields the sample state does not hold.
DANGLING_PATH = {
    "type": "score",
    "instructions": "How severe is the issue reported in `ticket.body`? Judge only the impact "
                    "on the customer's ability to use the product, using `ticket.priority` "
                    "and `ticket.affected_user_count` as well.",
    "criteria": [
        "Cosmetic; no impact to functionality",
        "Broken or degraded feature, but workaround exists",
        "Blocking issue; no workaround exists",
    ],
}

control = critique(SEVERITY_CONTROL, client=client, name="severity_control",
                   sample_state=SAMPLE_STATE, purpose="Sets the SLA timer for a ticket.")
absent = critique(MISSING_CONTEXT, client=client, name="missing_context",
                  sample_state=SAMPLE_STATE, purpose="Sets the SLA timer for a ticket.")
dangling = critique(DANGLING_PATH, client=client, name="dangling_path",
                    sample_state=SAMPLE_STATE, purpose="Sets the SLA timer for a ticket.")

check("a draft that defers to an absent policy outranks the known-good control",
      absent.signal("unstated_context") > control.signal("unstated_context"),
      f"absent={absent.signal('unstated_context'):.2f} "
      f"control={control.signal('unstated_context'):.2f}")

# The probability keys are the CRITIC's own levels, not the reviewed draft's -- the top level
# of `unstated_context` means "defers to something it does not contain".
control_u = next(f for f in control.findings if f.qid == "unstated_context")
absent_u = next(f for f in absent.findings if f.qid == "unstated_context")
levels = sorted(control_u.probabilities, key=int)
top, below_top = levels[-1], levels[-2]

check("the control does not read as deferring to absent context",
      control_u.probabilities[top] < absent_u.probabilities[top],
      f"control p(top)={control_u.probabilities[top]:.2f} "
      f"absent p(top)={absent_u.probabilities[top]:.2f}")

# The old failure was bimodal -- mass piled on the bottom level *and* spiked again on the top
# one -- so the mean was not a position at all, which is the trap MASTERY.md section 3 warns
# about. A healthy distribution decays toward the top instead of spiking at it. Measured
# before the split: p(2)=0.11 but p(3)=0.22, a spike. Asserting the shape, not a cutoff.
check("the control's distribution decays toward the top, with no second peak",
      control_u.probabilities[top] <= control_u.probabilities[below_top],
      f"p({below_top})={control_u.probabilities[below_top]:.2f} "
      f">= p({top})={control_u.probabilities[top]:.2f}  probs={control_u.probabilities}")

check("a dangling path is caught once a sample state is supplied",
      dangling.signal("dangling_state_path") > control.signal("dangling_state_path"),
      f"dangling={dangling.signal('dangling_state_path'):.2f} "
      f"resolving={control.signal('dangling_state_path'):.2f}")

bare = critique(SEVERITY_CONTROL, client=client, name="severity_control_no_state",
                purpose="Sets the SLA timer for a ticket.")
check("without a sample state the path check is reported unasked, not scored",
      "dangling_state_path" in dict(bare.not_checked)
      and all(f.qid != "dangling_state_path" for f in bare.findings),
      dict(bare.not_checked)["dangling_state_path"][:50])
check("supplying a sample state leaves nothing unasked", control.not_checked == [])
check("the unasked check is surfaced in the rendered report",
      "NOT CHECKED" in bare.render() and "dangling_state_path" in bare.render())
check("the unasked check is surfaced in JSON",
      [n["id"] for n in bare.to_dict()["not_checked"]] == ["dangling_state_path"])


# --- 7. The report ranks, and says what to fix ------------------------------------------------

print("\n[7] Output shape")
signals = [(f.advisory, -f.signal) for f in bad_c.findings]
check("findings are ranked, defects before advisory notes", signals == sorted(signals))
check("every finding carries a fix, not just a score",
      all(f.fix.strip() for f in bad_c.findings))
check("the report renders without error", "RANKED FINDINGS" in bad_c.render())

payload = bad_c.to_dict()
check("JSON output declares its bands provisional", payload["bands_are_provisional"] is True)
check("JSON output is serializable", isinstance(json.dumps(payload), str))
check("JSON findings carry id, signal and fix",
      all({"id", "signal", "fix"} <= set(f) for f in payload["findings"]))
check("cost and latency are reported", bad_c.input_tokens > 0 and bad_c.latency_ms > 0,
      f"{bad_c.input_tokens} tokens, ${bad_c.cost_usd:.8f}, {bad_c.latency_ms} ms")


# --- 8. Spend is attributable -----------------------------------------------------------------

print("\n[8] Usage log attribution")
if DEFAULT_USAGE_LOG.is_file():
    recent = [json.loads(line) for line in
              DEFAULT_USAGE_LOG.read_text(encoding="utf-8").splitlines()[-8:]]
    check("calls are tagged for spend attribution",
          any(r.get("tag") == TAG for r in recent),
          f"tag={TAG}")
else:
    check("usage log present", False, f"{DEFAULT_USAGE_LOG} missing")


total_cost = sum(r.cost_usd for r in (bad, good, bad_c, good_c, mistyped, right))
print(f"\n  6 live critiques, ${total_cost:.6f} total")
print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
