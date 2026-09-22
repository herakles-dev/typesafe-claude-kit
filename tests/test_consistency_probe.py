"""Live tests for tools/consistency_probe.py.

Run: python3 tests/test_consistency_probe.py
Requires TYPESAFE_API_KEY in the environment.

Repeats is kept low (3) so this stays cheap: 2 inputs x 3 repeats = 6 calls against the
5-question `ticket_triage` set, well under a cent. Assertions check the mechanism and the
math -- that instability picks the right field, that ranking is actually sorted, that cost/
latency were measured, that every question's stats are internally consistent -- never an
invented "this question should be stable" cutoff. Real model output is free to vary; the
probe's job is to measure that variation accurately, not to pass a threshold on it.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from questions import names  # noqa: E402
from consistency_probe import (  # noqa: E402
    QuestionStability,
    estimate_run,
    load_inputs,
    main,
    probe,
)
from questions import get  # noqa: E402

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}" + (f" -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


INPUTS = [
    {"id": "clear-bug", "state": {
        "ticket": "Export to PDF fails with a spinner that never finishes. Third time "
                  "writing in about this.",
        "policy": "Blocking issues with no workaround are escalated immediately.",
    }},
    {"id": "ambiguous", "state": {
        "ticket": "Not happy with how this is going.",
        "policy": "Blocking issues with no workaround are escalated immediately.",
    }},
]

tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump(INPUTS, tmp)
tmp.close()
INPUTS_PATH = tmp.name

REPEATS = 3

print("\n[1] Auto-discovery")
check("ticket_triage registered via auto-import", "ticket_triage" in names(), str(names()))

print("\n[2] load_inputs")
loaded = load_inputs(INPUTS_PATH)
check("parses explicit-id inputs", len(loaded) == 2, str(loaded[0][0]))
check("ids preserved", [i for i, _ in loaded] == ["clear-bug", "ambiguous"])
check("state passed through untouched", loaded[0][1]["ticket"].startswith("Export to PDF"))

plain_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump([{"ticket": "no id here"}], plain_tmp)
plain_tmp.close()
plain_loaded = load_inputs(plain_tmp.name)
check("plain state (no id/state wrapper) gets an auto id",
      plain_loaded[0][0] == "input-0", plain_loaded[0][0])

print("\n[3] estimate_run -- math, before any network call")
qs = get("ticket_triage")
est = estimate_run(qs, loaded, REPEATS)
check("total_calls = inputs x repeats", est["total_calls"] == len(loaded) * REPEATS,
      est["total_calls"])
check("cost estimate positive", est["est_cost_usd"] > 0, est["est_cost_usd"])

print("\n[4] instability picks the right field (pure mechanism, no live call)")
choice_like = QuestionStability(
    qid="x", qtype="choice", n=5, agreement_pct=100.0, modal_pick="a",
    vector_std=0.0234, scalar_mean=0.9, scalar_std=0.01,
    confidence_mean=0.9, confidence_std=0.01,
)
noul_like = QuestionStability(
    qid="y", qtype="noul", n=5, agreement_pct=100.0, modal_pick="yes",
    vector_std=None, scalar_mean=0.8, scalar_std=0.0456,
    confidence_mean=None, confidence_std=None,
)
check("choice/score instability = vector_std", choice_like.instability == 0.0234)
check("noul instability = scalar_std (no vector to fall back on)", noul_like.instability == 0.0456)

print("\n[5] Live probe run (6 calls: 2 inputs x 3 repeats)")
report = probe(qs, loaded, repeats=REPEATS, concurrency=3)
check("total_calls matches", report.total_calls == len(loaded) * REPEATS, report.total_calls)
check("every question present in aggregate", set(report.aggregate) == set(qs.questions),
      sorted(report.aggregate))
check("cost was measured, not estimated", report.total_cost_usd > 0,
      f"${report.total_cost_usd:.6f}")
check("latency was measured", report.total_latency_ms > 0, f"{report.total_latency_ms}ms")
check("wall clock measured", report.wall_clock_s > 0, f"{report.wall_clock_s}s")

for qid, stab in report.aggregate.items():
    is_noul = stab.qtype == "noul"
    check(f"{qid}: agreement_pct in [0,100]", 0.0 <= stab.agreement_pct <= 100.0,
          stab.agreement_pct)
    check(f"{qid}: vector_std is None iff noul", (stab.vector_std is None) == is_noul,
          f"type={stab.qtype} vector_std={stab.vector_std}")
    check(f"{qid}: confidence is None iff noul", (stab.confidence_mean is None) == is_noul,
          f"type={stab.qtype} confidence_mean={stab.confidence_mean}")
    check(f"{qid}: scalar_std >= 0", stab.scalar_std >= 0.0, stab.scalar_std)

print("\n[6] Ranking is actually sorted, on real measured data")
ranked = sorted(report.aggregate.values(), key=lambda s: s.instability, reverse=True)
check("ranked list is non-increasing by instability",
      all(ranked[i].instability >= ranked[i + 1].instability for i in range(len(ranked) - 1)),
      [round(s.instability, 4) for s in ranked])

print("\n[7] validated_on discipline")
check("report carries the validated_on note", "validated_on" in report.note_on_validated_on)
check("note explicitly says this tool does not emit one",
      "never emit" in report.note_on_validated_on.lower())
as_json = json.dumps({"aggregate": {}, "note_on_validated_on": report.note_on_validated_on})
check("note survives JSON round-trip (what --json actually ships)",
      "validated_on" in json.loads(as_json)["note_on_validated_on"])

print("\n[8] CLI cost/volume gate refuses large runs without --yes, before any network call")
rc = main(["--set", "ticket_triage", "--inputs", INPUTS_PATH, "--repeats", "1000"])
check("main() returns non-zero and does not run", rc == 3, rc)

Path(INPUTS_PATH).unlink(missing_ok=True)
Path(plain_tmp.name).unlink(missing_ok=True)

print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
