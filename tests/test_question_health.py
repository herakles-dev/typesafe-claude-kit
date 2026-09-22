"""Tests for tools/question_health.py.

Run: python3 tests/test_question_health.py
Requires TYPESAFE_API_KEY in the environment for the live sections.

question_health.py is one of six tools the portal (AGENTS.md) tells every agent to reach for,
and had zero tests before this file -- every other tool in tools/ has one.

METHODOLOGY, per AGENTS.md standing rules #2 and #3 and the sibling suites' own docstrings:
every live assertion here is a comparison between two constructed inputs -- one built to carry
information about the state, one built so it structurally cannot -- never a bare cutoff on a
single measured number. The tool's own DEAD_BELOW/WEAK_BELOW bands are, in its own words,
"for the printed verdict only... POLICY, not measured", so even they are only exercised here
against synthetic, fully-controlled answers (section [3]), never against a live model answer.

The tool's whole purpose is INFORMATION -- variation across inputs -- not accuracy. So the
paired live case in section [6] asserts that a question built to read `ticket` shows more
spread than a question built to ignore it, never that either one's absolute answer is
"correct". Section [4] reproduces the tool's own module-docstring example (a Noul whose 3
positives, out of 66 cases, all shared one http_status) against synthetic FakeResult data, so
the concentration mechanism is proven without depending on a live model reproducing that exact
finding on demand.

Sections [1]-[5] need no network at all: dig()'s path resolution and normalised_entropy()'s
math are pure functions; analyse()'s aggregation is exercised against a FakeResult stand-in
that exposes exactly the accessors it calls (.noul()/.choice()/.score()), so the aggregation
math is tested with zero model variance; main()'s CLI guards (missing args, empty/malformed
input, --limit slicing below the 2-input minimum, --workers' argparse type) are network-free
by construction -- every one of them raises before `TypeSafeClient()` is ever constructed in
the source, which section [5] relies on and states explicitly rather than assuming.
"""

import collections
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from questions import QuestionSet, get  # noqa: E402
from typesafe_client import TypeSafeClient, choice, noul, score  # noqa: E402
from question_health import (  # noqa: E402
    analyse,
    dig,
    load_inputs,
    main,
    normalised_entropy,
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


def run_main(argv):
    """Invoke question_health.main() the way a real CLI invocation would.

    main() -- unlike its four sibling tools (consistency_probe, jaggedness_screen,
    question_critic, confidence_accuracy_curve all take `argv: ... | None = None`) -- accepts
    no argv parameter and always reads sys.argv. That is a real discrepancy (see the report),
    and this helper works around it by patching sys.argv for the duration of the call rather
    than modifying the tool. Returns (return_code, system_exit); exactly one is not None.
    """
    old_argv = sys.argv
    sys.argv = ["question_health.py", *argv]
    try:
        try:
            return main(), None
        except SystemExit as exc:
            return None, exc
    finally:
        sys.argv = old_argv


class FakeResult:
    """Stand-in for typesafe_client.Result exposing exactly what analyse() calls on a result --
    .noul(qid) / .choice(qid) / .score(qid) -- so the aggregation math can be tested against
    answers we choose, with zero model variance and zero network."""

    def __init__(self, noul_vals=None, choice_val=None, score_val=None):
        self._noul = noul_vals or {}
        self._choice_val = choice_val
        self._score_val = score_val

    def noul(self, qid):
        return self._noul[qid]

    def choice(self, qid):
        return self._choice_val

    def score(self, qid):
        return self._score_val


temp_files = []


def make_temp_json(payload_or_text):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    if isinstance(payload_or_text, str):
        tmp.write(payload_or_text)
    else:
        json.dump(payload_or_text, tmp)
    tmp.close()
    temp_files.append(tmp.name)
    return tmp.name


# --- 1. dig() -- dotted path resolution (no API call) -----------------------------------------

print("\n[1] dig() -- dotted path resolution (no API call)")

STATE = {"probe": {"http_status": "429", "provider_message": "rate limited"},
         "model_entry": {"id": "some/model"}}

check("resolves a nested path", dig(STATE, "probe.http_status") == "429",
      dig(STATE, "probe.http_status"))
check("resolves a top-level path to a whole subtree",
      dig(STATE, "model_entry") == {"id": "some/model"})
check("a missing leaf under a real branch returns None",
      dig(STATE, "probe.nonexistent") is None)
check("a missing branch entirely returns None", dig(STATE, "nonexistent.path") is None)
check("a path into a non-dict value returns None instead of raising",
      dig("just a string", "a.b") is None)
check("None as the root object returns None instead of raising", dig(None, "a.b") is None)
check("deep nesting resolves all the way down", dig({"a": {"b": {"c": 42}}}, "a.b.c") == 42)


# --- 2. normalised_entropy() -- entropy math (no API call) ------------------------------------

print("\n[2] normalised_entropy() -- entropy math (no API call)")

check("an even split over exactly the options offered has entropy 1.0",
      abs(normalised_entropy(collections.Counter({"a": 5, "b": 5}), 2) - 1.0) < 1e-9)
check("a single option ever winning has entropy 0.0",
      normalised_entropy(collections.Counter({"a": 10}), 2) == 0.0)
check("normalising by options OFFERED, not observed: 2-of-6 reads lower than 2-of-2",
      normalised_entropy(collections.Counter({"a": 5, "b": 5}), 6)
      < normalised_entropy(collections.Counter({"a": 5, "b": 5}), 2))
check("no picks at all is defined as zero, not a division error",
      normalised_entropy(collections.Counter(), 3) == 0.0)


# --- 3. analyse() -- Score/Choice aggregation on synthetic, fixed answers (no API call) --------

print("\n[3] analyse() -- Score/Choice aggregation on synthetic, fixed answers (no API call)")

SCORE_SET = QuestionSet(
    name="question_health_test_score_fixture", version="1.0.0",
    description="Synthetic fixture: a Score question fed fixed answers, to test analyse()'s "
                "aggregation math with zero model variance.",
    questions={"level": score("irrelevant for this fixture -- answers are supplied directly",
                              ["a cosmetic situation", "a degraded situation",
                               "a blocking situation"])},
)

constant_score = [FakeResult(score_val=1.0) for _ in range(5)]
constant_report = analyse(SCORE_SET, constant_score)["level"]
check("a Score answering the same value every time has zero spread",
      constant_report["spread"] == 0.0, constant_report["spread"])
check("...and is verdict DEAD under the tool's own printed-report bands",
      constant_report["verdict"] == "DEAD", constant_report["verdict"])
check("...and never separates (only one rounded level ever wins)",
      constant_report["separates"] is False)

varied_score = [FakeResult(score_val=v) for v in [0.0, 0.0, 2.0, 2.0, 1.0]]
varied_report = analyse(SCORE_SET, varied_score)["level"]
check("a Score that actually moves across inputs carries more information than a constant one",
      varied_report["information"] > constant_report["information"],
      f"varied={varied_report['information']:.3f} constant={constant_report['information']:.3f}")
check("...and separates (more than one rounded level wins)",
      varied_report["separates"] is True, varied_report["distinct_rounded"])

CHOICE_SET = QuestionSet(
    name="question_health_test_choice_fixture", version="1.0.0",
    description="Synthetic fixture: a Choice question fed fixed answers, same purpose as above.",
    questions={"route": choice("irrelevant for this fixture -- answers are supplied directly",
                               {"a": None, "b": None, "c": None})},
)

constant_choice = [FakeResult(choice_val="a") for _ in range(6)]
constant_c_report = analyse(CHOICE_SET, constant_choice)["route"]
check("a Choice that always picks the same option out of three has zero information",
      constant_c_report["information"] == 0.0, constant_c_report["information"])
check("...and does not separate", constant_c_report["separates"] is False)

varied_choice = [FakeResult(choice_val=v) for v in ["a", "b", "c", "a", "b", "c"]]
varied_c_report = analyse(CHOICE_SET, varied_choice)["route"]
check("a Choice that spreads across its options carries more information than a constant one",
      varied_c_report["information"] > constant_c_report["information"],
      f"varied={varied_c_report['information']:.3f} constant={constant_c_report['information']:.3f}")


# --- 4. analyse() -- group_by concentration mechanism (no API call) ---------------------------

print("\n[4] analyse() -- group_by concentration mechanism (no API call)")
print("    reproduces the tool's own module-docstring example: 3 positives out of 66 cases, "
      "all sharing one http_status -- coverage that LOOKS real until you group it.")

NOUL_SET = QuestionSet(
    name="question_health_test_noul_fixture", version="1.0.0",
    description="Synthetic fixture reproducing the docstring's concentration example.",
    questions={"claim_holds": noul("irrelevant for this fixture -- answers are supplied directly")},
)

statuses = ["200"] * 30 + ["404"] * 30 + ["429"] * 3 + ["403"] * 3  # 66 cases, matching the docstring
concentrated_inputs = [{"id": f"i{i}", "state": {"probe": {"http_status": s}}}
                       for i, s in enumerate(statuses)]
concentrated_results = [
    FakeResult(noul_vals={"claim_holds": 0.9 if s == "429" else 0.1}) for s in statuses
]
report = analyse(NOUL_SET, concentrated_results, concentrated_inputs, group_by="probe.http_status")
entry = report["claim_holds"]

check("exactly the 3 planted positives are counted", entry["above_half"] == 3, entry["above_half"])
check("every positive is flagged as concentrated in a single group",
      entry["positives_concentrated"] is True, entry["positive_groups"])
check("the concentrated group is the one that actually supplied every positive",
      entry["positive_groups"] == {"429": 3}, entry["positive_groups"])
check("negatives are correctly spread across the other three groups",
      set(entry["negative_groups"]) == {"200", "404", "403"}, entry["negative_groups"])

# Control: positives spread across two groups must NOT be flagged concentrated.
mixed_results = [
    FakeResult(noul_vals={"claim_holds": 0.9 if s in ("200", "429") else 0.1}) for s in statuses
]
mixed_entry = analyse(NOUL_SET, mixed_results, concentrated_inputs,
                      group_by="probe.http_status")["claim_holds"]
check("positives spread across more than one group are NOT flagged as concentrated",
      mixed_entry["positives_concentrated"] is False, mixed_entry["positive_groups"])


# --- 5. main() CLI guards -- no API call reached in any of these -------------------------------

print("\n[5] main() CLI guards (no API call -- TypeSafeClient() is constructed only after "
      "these checks in the source, so none of this can reach the network)")

rc, exc = run_main([])
check("missing required --set/--inputs is refused by argparse itself",
      exc is not None and getattr(exc, "code", None) == 2, getattr(exc, "code", None))

empty_path = make_temp_json([])
rc, exc = run_main(["--set", "ticket_triage", "--inputs", empty_path])
check("an empty input file trips the <2-inputs guard before any network call",
      exc is not None and "at least 2 inputs" in str(exc), str(exc))

malformed_shape_path = make_temp_json({"this": "is a dict, not a list"})
expect_raises("a well-formed-JSON-but-wrong-shape input file is rejected by load_inputs()",
              SystemExit, lambda: load_inputs(Path(malformed_shape_path)))

malformed_json_path = make_temp_json("{not even valid json")
# Not a fix target (see task instructions) -- documented as a rough edge in the report instead:
# unlike the wrong-shape case above, syntactically invalid JSON is not caught gracefully; the
# raw json.JSONDecodeError propagates rather than load_inputs()'s own SystemExit message.
expect_raises("syntactically invalid JSON is not caught gracefully (raw JSONDecodeError propagates)",
              json.JSONDecodeError, lambda: load_inputs(Path(malformed_json_path)))

three_path = make_temp_json([
    {"id": "a", "state": {"ticket": "x"}},
    {"id": "b", "state": {"ticket": "y"}},
    {"id": "c", "state": {"ticket": "z"}},
])

rc, exc = run_main(["--set", "ticket_triage", "--inputs", three_path, "--limit", "1"])
check("--limit truncates the input list before the network call "
      "(limit=1 on 3 inputs trips the <2-inputs guard that 3 inputs alone would not)",
      exc is not None and "at least 2 inputs" in str(exc), str(exc))

rc, exc = run_main(["--set", "ticket_triage", "--inputs", three_path,
                    "--limit", "1", "--workers", "7"])
check("--workers parses as an int and is not itself what trips the guard above",
      exc is not None and "at least 2 inputs" in str(exc), str(exc))

rc, exc = run_main(["--set", "ticket_triage", "--inputs", three_path,
                    "--limit", "1", "--workers", "not-an-int"])
check("--workers rejects a non-integer value at the argparse level, before any network call",
      exc is not None and getattr(exc, "code", None) == 2, getattr(exc, "code", None))


# --- 6. Live -- a question that reads state vs. one that ignores it (paired, comparative) -----

print("\n[6] Live -- a question that reads state vs. one that ignores it (paired, comparative)")

client = TypeSafeClient()
TICKET_TRIAGE = get("ticket_triage")

PAIRED_SET = QuestionSet(
    name="question_health_test_paired_fixture", version="1.0.0",
    description="severity (reads `ticket`, reused verbatim from the proven ticket_triage set) "
                "vs. a general-knowledge Noul that never reads `ticket` at all.",
    questions={
        "severity": TICKET_TRIAGE.questions["severity"],
        "unrelated_fact": noul("Is Paris the capital of France?"),
    },
)

TICKETS = [
    "Just a heads up, the new icon looks a little small on my 4k monitor -- not a big deal.",
    "Export to CSV stopped working for me this morning, but PDF export is still fine.",
    "CSV export has been broken all week and my whole team has no way to get their data out.",
    "The entire billing dashboard has been down for three straight days; we cannot invoice "
    "anyone and there is no workaround.",
]
paired_inputs = [{"id": f"t{i}", "state": {"ticket": t}} for i, t in enumerate(TICKETS)]
paired_results = [PAIRED_SET.ask(client, item["state"], allow_unvalidated=True)
                  for item in paired_inputs]

paired_report = analyse(PAIRED_SET, paired_results, paired_inputs)
sev, fact = paired_report["severity"], paired_report["unrelated_fact"]

check("the question that reads `ticket` carries more information than the one that ignores it",
      sev["information"] > fact["information"],
      f"severity={sev['information']:.3f} unrelated_fact={fact['information']:.3f}")
check("severity actually reaches more than one rounded level across a cosmetic-to-blocking spread",
      sev["distinct_rounded"] > 1, sev["distinct_rounded"])


# --- 7. Live -- main() end to end: registered set, --limit, --group-by, --json ------------------

print("\n[7] Live -- main() end-to-end: registered set, --limit, --group-by, --json")

cli_inputs_path = make_temp_json([
    {"id": "cosmetic", "state": {
        "ticket": "Icon is a little small on my monitor, otherwise everything works fine.",
        "policy": "Blocking issues with no workaround are escalated immediately."}},
    {"id": "blocking", "state": {
        "ticket": "Export has been fully broken for a week; no workaround; we cannot get any "
                  "data out at all.",
        "policy": "Blocking issues with no workaround are escalated immediately."}},
    {"id": "never-sent", "state": {
        "ticket": "This third input should be truncated away by --limit and never reach the "
                  "model.",
        "policy": "Blocking issues with no workaround are escalated immediately."}},
])
cli_json_out = make_temp_json([])  # placeholder; overwritten by --json below

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc, exc = run_main(["--set", "ticket_triage", "--inputs", cli_inputs_path,
                        "--limit", "2", "--group-by", "policy",
                        "--json", cli_json_out])
stdout_text = buf.getvalue()

check("main() returns 0 on a valid registered set + inputs", exc is None and rc == 0, (rc, exc))
check("the report is printed to stdout", "QUESTION HEALTH" in stdout_text)
check("the json-write confirmation is printed", f"wrote {cli_json_out}" in stdout_text)

written = json.loads(Path(cli_json_out).read_text())
check("json output records the right set@version",
      written["set"] == "ticket_triage@1.0.0", written["set"])
check("--limit=2 is reflected in n_inputs (the 3rd input never reached the model)",
      written["n_inputs"] == 2, written["n_inputs"])
check("json output covers every ticket_triage question",
      set(written["questions"]) == set(TICKET_TRIAGE.questions), sorted(written["questions"]))

cost_match = re.search(r"(\d+) calls, \$([\d.]+)", stdout_text)
main_calls = int(cost_match.group(1)) if cost_match else 0
main_cost = float(cost_match.group(2)) if cost_match else 0.0
check("cost and call count are reported for the live run",
      main_calls == 2 and main_cost > 0, (main_calls, main_cost))


# --- 8. Cost/volume gate ------------------------------------------------------------------------

print("\n[8] Cost/volume gate")
print("    question_health.py has NO --yes/cost-confirmation gate, unlike consistency_probe.py's")
print("    refusal of large --repeats runs before any network call. Every input in the file is")
print("    sent to the model with no size warning or opt-in, regardless of file size. Not fixed")
print("    here per task instructions -- flagged in the report as a gap worth considering.")


# --- Wrap-up --------------------------------------------------------------------------------

paired_cost = sum(r.cost_usd for r in paired_results)
total_cost = paired_cost + main_cost
total_calls = len(paired_results) + main_calls

for f in temp_files:
    Path(f).unlink(missing_ok=True)

print(f"\n  {total_calls} live calls ({len(paired_results)} paired + {main_calls} CLI), "
      f"${total_cost:.6f} total")
print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
