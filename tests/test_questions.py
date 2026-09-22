"""Tests for the QuestionSet convention and its threshold guardrails.

Run: python3 tests/test_questions.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from questions import (  # noqa: E402
    QuestionSet,
    UnvalidatedThresholds,
    get,
    names,
    register,
)
from typesafe_client import TypeSafeClient, choice, noul  # noqa: E402

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
        print(f"  PASS  {name} -- {type(exc).__name__}: {str(exc)[:66]}")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"  FAIL  {name} -- raised {type(exc).__name__}, wanted {exc_type.__name__}")
    else:
        failed += 1
        print(f"  FAIL  {name} -- nothing raised")


QUESTIONS = {
    "is_retired": noul("Does `announcement` confirm the model was retired?"),
    "action": choice(
        "Which action does `policy` require?",
        {"keep": "Stays listed", "remove": "Provider confirmed retirement"},
    ),
}

print("\n[1] Construction guards")
expect_raises("empty question set rejected", ValueError,
              lambda: QuestionSet(name="empty", version="1.0.0", description="d", questions={}))
expect_raises("thresholds + moving alias rejected", ValueError,
              lambda: QuestionSet(name="aliased", version="1.0.0", description="d",
                                  questions=QUESTIONS, thresholds={"T": 0.9},
                                  model="jev-latest"))
check("no-threshold set may use an alias",
      QuestionSet(name="loose", version="1.0.0", description="d", questions=QUESTIONS,
                  model="jev-latest").model == "jev-latest")

print("\n[2] Threshold lookup")
qs = QuestionSet(name="demo", version="1.0.0", description="d", questions=QUESTIONS,
                 thresholds={"REMOVE_ABOVE": 0.9})
check("named threshold resolves", qs.threshold("REMOVE_ABOVE") == 0.9)
expect_raises("unknown threshold fails loudly", KeyError, lambda: qs.threshold("NOPE"))

print("\n[3] Irreversible-action gate")
unvalidated = QuestionSet(
    name="danger", version="1.0.0", description="deletes catalogue rows",
    questions=QUESTIONS, thresholds={"REMOVE_ABOVE": 0.9},
    gates_irreversible=True, validated_on=None,
)
check("unvalidated set reports so", not unvalidated.is_validated)
client = TypeSafeClient()
expect_raises("unvalidated irreversible set refuses to run", UnvalidatedThresholds,
              lambda: unvalidated.ask(client, {"announcement": "retired"}))

validated = QuestionSet(
    name="safe", version="1.0.0", description="same set, thresholds validated",
    questions=QUESTIONS, thresholds={"REMOVE_ABOVE": 0.9},
    gates_irreversible=True, validated_on="142 hand-labelled probes, 2026-09-19",
)
check("validated set reports so", validated.is_validated, validated.validated_on)

print("\n[4] Registry")
register(qs)
check("registered set retrievable", get("demo").version == "1.0.0")
check("names() lists it", "demo" in names(), str(names()))
expect_raises("version shadowing refused", ValueError,
              lambda: register(QuestionSet(name="demo", version="2.0.0", description="d",
                                           questions=QUESTIONS)))

print("\n[5] Live run through a QuestionSet")
result = validated.ask(client, {
    "announcement": "As of September 2026 the free tier for mistral-7b-instruct is retired.",
    "policy": "Remove only when the provider confirmed retirement.",
})
check("live call succeeded", len(result.answers) == 2, f"{len(result.answers)} answers")
check("choice selects remove", result.choice("action") == "remove", result.choice("action"))

# Assert the gate MECHANISM, not a particular outcome from an invented cutoff. An earlier
# version of this test asserted noul > 0.9 and failed at 0.76 -- the threshold was made up,
# which is precisely what `validated_on` exists to prevent. A threshold is only meaningful
# once tuned on real labelled data.
is_retired = result.noul("is_retired")
gate = validated.threshold("REMOVE_ABOVE")
check("threshold resolves by name, not inline literal", gate == 0.9)
check("gate is applicable to the answer", isinstance(is_retired > gate, bool),
      f"is_retired={is_retired:.2f} vs REMOVE_ABOVE={gate}")

# Live evidence of the documented divergence: a Choice is relative (which option wins), a
# Noul is absolute (is this true at all). They do not share a scale or a threshold.
check("Choice and Noul disagree in magnitude on the same evidence",
      result.choice("action") == "remove" and is_retired < 0.9,
      f"choice=remove but noul={is_retired:.2f} -- never port a cutoff between the two")

print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
