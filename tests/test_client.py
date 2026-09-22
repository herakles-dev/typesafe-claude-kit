"""Smoke tests for the TypeSafe client.

Run: python3 tests/test_client.py
Requires TYPESAFE_API_KEY in the environment.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from typesafe_client import (  # noqa: E402
    DEFAULT_MODEL,
    TypeSafeClient,
    choice,
    noul,
    score,
)

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
        print(f"  PASS  {name} -- {type(exc).__name__}: {str(exc)[:70]}")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"  FAIL  {name} -- raised {type(exc).__name__}, wanted {exc_type.__name__}")
    else:
        failed += 1
        print(f"  FAIL  {name} -- nothing raised")


print("\n[1] Local validation (no network)")
expect_raises("Score rejects 1 level", ValueError, lambda: score("x", ["only"]))
expect_raises("Score rejects 11 levels", ValueError, lambda: score("x", [str(i) * 3 for i in range(11)]))
expect_raises("Score rejects bare-number levels", ValueError, lambda: score("x", ["0", "1", "2"]))
expect_raises("Choice rejects 256 options", ValueError,
              lambda: choice("x", {f"o{i}": None for i in range(256)}))
expect_raises("Choice rejects empty criteria", ValueError, lambda: choice("x", {}))
expect_raises("Noul rejects stray criteria keys", ValueError,
              lambda: noul("x", {"true": "y", "maybe": "z"}))
check("Score accepts descriptive levels", score("x", ["a cosmetic issue", "a blocking issue"])["type"] == "score")
check("Noul accepts true/false criteria", "criteria" in noul("x", {"true": "y", "false": "n"}))

print("\n[2] Key loading + model pinning")
client = TypeSafeClient()
check("key loaded", bool(client.api_key), f"{len(client.api_key)} chars")
check("model is pinned, not aliased", client.model == DEFAULT_MODEL == "jev-1.13.0", client.model)

print("\n[3] GET /v1/models")
models = client.list_models()
check("models returned", len(models) > 0, ", ".join(m["name"] for m in models))

print("\n[4] Live multi-primitive call")
result = client.ask(
    state={
        "ticket": "Export to PDF fails with a spinner that never finishes. Some of our team "
                  "say CSV export still works, others say it fails too. Third time writing in.",
        "policy": "Blocking issues with no workaround are escalated immediately.",
    },
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
    tag="smoke-test",
)

check("HTTP 200 with answers", len(result.answers) == 5, f"{len(result.answers)} answers")
check("responding model reported", result.model == "jev-1.13.0", result.model)
check("choice accessor", result.choice("team") == "engineering", result.choice("team"))
check("confidence accessor", 0.0 <= result.confidence("team") <= 1.0,
      f"{result.confidence('team'):.2f}")
check("noul accessor", result.noul("repeat_contact") > 0.5,
      f"repeat_contact={result.noul('repeat_contact'):.2f}")
check("refund correctly absent", result.noul("refund_requested") < 0.5,
      f"refund={result.noul('refund_requested'):.2f}")
check("score in range", 0.0 <= result.score("severity") <= 2.0, f"{result.score('severity'):.2f}")

# Normalization matters: severity tops out at 2, report_quality at 3.
sev_n = result.normalized_score("severity")
rq_n = result.normalized_score("report_quality")
check("normalized scores on 0-1", 0.0 <= sev_n <= 1.0 and 0.0 <= rq_n <= 1.0,
      f"severity={sev_n:.2f} report_quality={rq_n:.2f}")
check("normalization differs from raw", abs(rq_n - result.score("report_quality")) > 0.01,
      f"raw={result.score('report_quality'):.2f} norm={rq_n:.2f}")

check("probabilities sum to 1", abs(sum(result.probabilities("severity").values()) - 1.0) < 0.02)

print("\n[5] Accessor type safety")
expect_raises("confidence() rejects a Noul", TypeError, lambda: result.confidence("refund_requested"))
expect_raises("choice() rejects a Score", TypeError, lambda: result.choice("severity"))
expect_raises("unknown question id", KeyError, lambda: result.noul("nope"))

print("\n[6] Cost + latency")
check("usage recorded", result.input_tokens > 0, f"{result.input_tokens} input tokens")
check("cost computed", result.cost_usd > 0, f"${result.cost_usd:.8f}")
print(f"        latency: {result.latency_ms} ms")

print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
