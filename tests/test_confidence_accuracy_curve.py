"""Live tests for tools/confidence_accuracy_curve.py.

Run: python3 tests/test_confidence_accuracy_curve.py
Requires TYPESAFE_API_KEY in the environment.

12 calls against the `ticket_triage` set (4 labelled cases from
`examples/labels/ticket_triage.json`, each evaluated once directly and once via each of the
two CLI invocations below), well under a cent. The 4 cases are picked to cover more than one
`team` outcome (engineering/billing/other) so the correctness logic is exercised on more than
one branch, not just to reproduce the full labelled set (that belongs to typesafe-calibrator's
own session, not this test).
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from questions import get  # noqa: E402
from confidence_accuracy_curve import (  # noqa: E402
    BandsReport,
    band_report,
    cut_table,
    evaluate,
    load_labelled,
    main,
    reliability_bins,
    resolve_map,
    resolve_transform,
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


ALL_LABELS = json.loads((ROOT / "examples" / "labels" / "ticket_triage.json").read_text())
# Two `engineering`, one `billing`, one `other` -- exercises more than the majority class.
SUBSET_IDX = [0, 1, 4, 6]
SUBSET = [ALL_LABELS[i] for i in SUBSET_IDX]

tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump(SUBSET, tmp)
tmp.close()
LABELS_PATH = tmp.name

qs = get("ticket_triage")

print("\n[1] load_labelled")
cases = load_labelled(LABELS_PATH, expected_key="expected_team")
check("parses all rows", len(cases) == len(SUBSET), len(cases))
check("expected pulled from expected_team", cases[0].expected == SUBSET[0]["expected_team"])
check("state passed through untouched",
      cases[0].state == SUBSET[0]["state"])

print("\n[2] load_labelled fails loudly on a missing expected key")
try:
    load_labelled(LABELS_PATH, expected_key="not_a_real_field")
    check("raises SystemExit on missing expected field", False)
except SystemExit as exc:
    check("raises SystemExit on missing expected field", True, str(exc))

print("\n[3] resolve_map -- inline JSON, and None means no translation")
inline = resolve_map('{"engineering": "eng"}')
check("inline JSON map parsed", inline == {"engineering": "eng"})
check("resolve_map(None) is None (identity comparison, no map)", resolve_map(None) is None)

print("\n[4] resolve_transform -- identity is the only built-in transform")
identity = resolve_transform(None)
check("identity transform returns input unchanged", identity({"x": 1}) == {"x": 1})
identity_named = resolve_transform("identity")
check("'identity' string resolves the same way as None",
      identity_named(SUBSET[0]["state"]) == SUBSET[0]["state"])

print("\n[5] Live evaluate() run (4 calls, one per labelled case)")
judged = evaluate(qs, cases, "team", workers=4)
check("one Judged per case", len(judged) == len(cases), len(judged))
check("no errors on well-formed cases", all(j.error is None for j in judged),
      [j.error for j in judged if j.error])
check("metric_kind is confidence for a Choice question",
      all(j.metric_kind == "confidence" for j in judged), {j.metric_kind for j in judged})
check("metric in [0,1]", all(0.0 <= j.metric <= 1.0 for j in judged),
      [j.metric for j in judged])
check("predicted is one of the question's own option names (no --map supplied)",
      all(j.predicted in {"engineering", "billing", "other"} for j in judged),
      [j.predicted for j in judged])
check("cost was measured per call", all(j.cost_usd > 0 for j in judged),
      [j.cost_usd for j in judged])
n_correct = sum(j.correct for j in judged)
check("correctness matches expected vs predicted directly",
      all((j.predicted == j.expected) == j.correct for j in judged))
print(f"    accuracy on this 4-case mix: {n_correct}/{len(judged)}")

print("\n[6] reliability_bins -- mechanism, on the real judged output")
bins = reliability_bins(judged, n_bins=5)
check("5 bins requested, 5 returned", len(bins) == 5, len(bins))
check("bin ns sum to the number of scored cases",
      sum(b.n for b in bins) == sum(1 for j in judged if j.error is None),
      (sum(b.n for b in bins), len(judged)))
check("bin edges are contiguous 0..1", bins[0].lo == 0.0 and bins[-1].hi == 1.0,
      (bins[0].lo, bins[-1].hi))
check("empty bins report accuracy=None, not 0.0 (would misread as 'confidently wrong')",
      all((b.n == 0) == (b.accuracy is None) for b in bins))

print("\n[7] cut_table -- mechanism")
cuts = cut_table(judged)
check("cut 0.0 includes every scored case",
      cuts[0].n_at_or_above == sum(1 for j in judged if j.error is None), cuts[0].n_at_or_above)
check("n_at_or_above is non-increasing as the cut rises",
      all(cuts[i].n_at_or_above >= cuts[i + 1].n_at_or_above for i in range(len(cuts) - 1)),
      [c.n_at_or_above for c in cuts])
check("automation_rate = n_at_or_above / total",
      all(abs(c.automation_rate - c.n_at_or_above / len(judged)) < 1e-6 for c in cuts))
check("error_rate = 1 - accuracy wherever both are defined",
      all(c.error_rate is None or abs(c.error_rate - (1 - c.accuracy)) < 1e-6 for c in cuts))

print("\n[8] band_report -- refuses to invent a cutoff, honors one when given")
no_bands = band_report(judged, None, None)
check("no low/high -> no bands computed", no_bands.bands == [], no_bands.bands)
check("says why, explicitly", "does not invent" in no_bands.note)

with_bands = band_report(judged, 0.5, 0.9)
names_seen = {b.name for b in with_bands.bands}
check("three named bands present", names_seen == {"act", "review", "do_not_act"}, names_seen)
total_n = sum(b.n for b in with_bands.bands)
check("bands partition every scored case exactly once",
      total_n == sum(1 for j in judged if j.error is None), total_n)

try:
    band_report(judged, 0.9, 0.5)
    check("low > high raises", False)
except ValueError:
    check("low > high raises", True)

print("\n[9] CLI end to end, human and JSON output")
rc = main(["--set", "ticket_triage", "--labels", LABELS_PATH, "--answer", "team",
           "--expected", "expected_team", "--low", "0.5", "--high", "0.9", "--json"])
check("CLI exits 0", rc == 0, rc)

out_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
out_tmp.close()
rc2 = main(["--set", "ticket_triage", "--labels", LABELS_PATH, "--answer", "team",
            "--expected", "expected_team", "--json-out", out_tmp.name])
check("CLI --json-out exits 0", rc2 == 0, rc2)
written = json.loads(Path(out_tmp.name).read_text())
check("written report has judged/reliability_bins/cut_table/bands",
      {"judged", "reliability_bins", "cut_table", "bands"} <= set(written), sorted(written))
check("no validated_on key anywhere in the written report (this tool never emits one)",
      "validated_on" not in json.dumps(written))

print("\n[10] Unknown question id fails loudly, before any call")
try:
    evaluate(qs, cases, "not_a_real_question")
    check("unknown --answer raises KeyError", False)
except KeyError as exc:
    check("unknown --answer raises KeyError", True, str(exc))

Path(LABELS_PATH).unlink(missing_ok=True)
Path(out_tmp.name).unlink(missing_ok=True)

print(f"\n{'=' * 60}\n  {passed} passed, {failed} failed\n{'=' * 60}")
sys.exit(1 if failed else 0)
