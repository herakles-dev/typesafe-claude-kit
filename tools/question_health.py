#!/usr/bin/env python3
"""question-health -- does this question carry any information?

THE GAP THIS FILLS. Three orthogonal properties decide whether a question is worth asking, and
only two of them were measured anywhere in this repo:

  1. STABLE      does it hold still across repeats?      -> tools/consistency_probe.py
  2. ACCURATE    does it agree with ground truth?        -> typesafe-calibrator
  3. INFORMATIVE does its answer depend on the input?    -> nobody. this file.

A question can be perfectly stable and perfectly accurate and still be useless: if it returns
0.95 for every input you will ever send it, it has told you nothing about any of them. It is a
constant wearing the costume of a judgment.

This never mattered before because asking a question was expensive enough that you only asked
ones you needed. Speculative fan-out inverts that -- a caller can fire dozens of questions per
request because extra questions are nearly free -- and the moment you stop rationing questions,
dead ones accumulate silently. They cost tokens, pad the state/question budget, and are
indistinguishable from working questions unless you measure variation across inputs.

WHAT IS MEASURED. One call per input (repeats are the consistency probe's job). Then per
question, across inputs:

  Noul    spread of the probability, and how many inputs fall on each side of 0.5. A Noul that
          never crosses 0.5 cannot gate anything, however confidently it answers.
  Choice  normalised entropy over which option won, plus the count of options that ever won. A
          Choice that always picks the same option out of six is a constant with overhead.
  Score   spread of the score, and how many distinct levels ever won.

Entropy is normalised by log(k) over the options actually offered, so a 2-option and a
6-option Choice are comparable.

HOW TO READ IT. Low information is a finding, not automatically a defect:
  - a question genuinely irrelevant to this input set (right question, wrong corpus)
  - a question whose wording collapses distinctions that matter (send to question-smith)
  - a genuinely dead question that should be deleted to buy back tokens
Only inputs you actually expect in production make this meaningful. Feed it a uniform sample
and every question will look dead -- correctly.

LIMITATION, measured 2026-09-20: this tool counts answers, it does not read them. It cannot
tell a positive that fired for the intended reason from one that fired for an unintended one,
so a high count is NOT evidence of usable coverage.

Found the hard way on an internal availability screen. This tool reported one Noul crossing
0.5 on 3 of 66 audit cases, which looked like thin-but-real coverage. Reading those three
showed all of them were rate-limited responses where the model had latched onto the
*rate-limit reset window* as satisfying the question -- correct on the wording, and entirely
the wrong class of positive for a threshold meant to gate a different condition. Usable
coverage was zero, not three.

So: treat every count here as an upper bound on coverage, and read the cases before any of
them backs a threshold. The counts are good at proving a question is DEAD (zero variation
cannot hide a reason) and unreliable at proving one is ALIVE.

Usage:
    python3 tools/question_health.py --set ticket_triage --inputs examples/labels/ticket_triage.json
    python3 tools/question_health.py --set ticket_triage --inputs f.json --json out.json
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from questions import get, names  # noqa: E402
from typesafe_client import TypeSafeClient  # noqa: E402

# Thresholds for the printed verdict only. POLICY, not measured -- they shape a report a human
# reads, they gate nothing, and every underlying number is printed so you can disagree.
DEAD_BELOW = 0.02       # spread below this: the answer barely moves across inputs
WEAK_BELOW = 0.10


def normalised_entropy(counts: collections.Counter, k_offered: int) -> float:
    """Shannon entropy of the winning-option distribution, scaled to 0-1.

    Normalising by log(k_offered) rather than log(k_observed) is deliberate: a Choice that
    offers six options and only ever picks two should read as low information, not as
    'perfectly balanced over the two it uses'.
    """
    total = sum(counts.values())
    if total == 0 or k_offered <= 1:
        return 0.0
    h = -sum((n / total) * math.log(n / total) for n in counts.values() if n)
    return h / math.log(k_offered)


def load_inputs(path: pathlib.Path) -> list[dict]:
    """Accept a bare list of states, or records carrying a `state` key (label files)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("inputs must be a JSON list")
    out = []
    for i, item in enumerate(raw):
        if isinstance(item, dict) and "state" in item:
            out.append({"id": item.get("id", f"input-{i}"), "state": item["state"]})
        else:
            out.append({"id": f"input-{i}", "state": item})
    return out


def dig(obj, path: str):
    """Follow a dotted path into a nested dict. Returns None rather than raising."""
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def analyse(qs, results: list, inputs: list[dict] | None = None,
            group_by: str | None = None) -> dict:
    """Per-question variation across inputs.

    `group_by` is a dotted path into each input's state. When given, the inputs whose answer
    landed positive are grouped by that field. This is the closest mechanical handle on the
    blind spot in the LIMITATION above: the tool still cannot read *why* a question fired, but
    if every positive comes from one input class that concentration is visible, and it is the
    cheapest signal that the positives may all be the same wrong kind.

    It would have caught the availability-screen case outright -- 3 positives, all from one
    input class -- which otherwise took reading the cases by hand to notice.
    """
    report: dict[str, dict] = {}
    groups = ([dig(i["state"], group_by) for i in inputs]
              if (group_by and inputs) else [None] * len(results))

    for qid, qdef in qs.questions.items():
        qtype = qdef["type"]
        entry: dict = {"type": qtype}

        if qtype == "noul":
            vals = [r.noul(qid) for r in results]
            if group_by:
                pos = collections.Counter(g for g, v in zip(groups, vals) if v >= 0.5)
                neg = collections.Counter(g for g, v in zip(groups, vals) if v < 0.5)
                entry["positive_groups"] = dict(pos.most_common())
                entry["negative_groups"] = dict(neg.most_common())
                # One class supplying every positive is the pattern worth a second look.
                entry["positives_concentrated"] = len(pos) == 1 and sum(pos.values()) > 0
            entry["spread"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            entry["min"], entry["max"] = min(vals), max(vals)
            entry["above_half"] = sum(v >= 0.5 for v in vals)
            entry["below_half"] = sum(v < 0.5 for v in vals)
            # A Noul that never crosses 0.5 cannot gate a binary decision, whatever its spread.
            entry["separates"] = entry["above_half"] > 0 and entry["below_half"] > 0
            entry["information"] = entry["spread"]

        elif qtype == "choice":
            picks = collections.Counter(r.choice(qid) for r in results)
            k = len(qdef["criteria"])
            entry["options_offered"] = k
            entry["options_used"] = len(picks)
            entry["distribution"] = dict(picks.most_common())
            entry["information"] = normalised_entropy(picks, k)
            entry["separates"] = len(picks) > 1

        elif qtype == "score":
            vals = [r.score(qid) for r in results]
            levels = len(qdef["criteria"])
            entry["levels"] = levels
            entry["spread"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            entry["min"], entry["max"] = min(vals), max(vals)
            entry["distinct_rounded"] = len({round(v) for v in vals})
            # Normalise by the scale length so a 4-level and a 10-level Score compare.
            entry["information"] = entry["spread"] / max(levels - 1, 1)
            entry["separates"] = entry["distinct_rounded"] > 1

        info = entry["information"]
        entry["verdict"] = ("DEAD" if info < DEAD_BELOW
                            else "weak" if info < WEAK_BELOW else "informative")
        report[qid] = entry

    return report


def render(qs, report: dict, n_inputs: int, cost: float, calls: int) -> str:
    out: list[str] = []
    w = out.append
    w(f"\nQUESTION HEALTH  --  {qs.name}@{qs.version}  ({n_inputs} inputs, one call each)")
    w("")
    w("information = how much the answer varies ACROSS INPUTS. It is not accuracy and not")
    w("stability. A question can be stable, accurate, and carry no information at all.")
    w("")
    # Size the id column to the longest name present -- a fixed width runs long ids into the
    # type column, and it does so worst for the descriptive names worth reading.
    cw = max([len(q) for q in report] + [len("question")]) + 2
    w(f"{'question':{cw}}{'type':8}{'info':>7}  {'sep':>4}  detail")
    w("-" * (cw + 62))

    for qid, e in sorted(report.items(), key=lambda kv: kv[1]["information"]):
        sep = "yes" if e["separates"] else "NO"
        if e["type"] == "noul":
            detail = (f"range {e['min']:.2f}-{e['max']:.2f}, "
                      f"{e['above_half']} above / {e['below_half']} below 0.5")
        elif e["type"] == "choice":
            top = list(e["distribution"].items())[:3]
            detail = (f"{e['options_used']}/{e['options_offered']} options ever won: "
                      + ", ".join(f"{k}={v}" for k, v in top))
        else:
            detail = (f"range {e['min']:.2f}-{e['max']:.2f} over {e['levels']} levels, "
                      f"{e['distinct_rounded']} distinct")
        w(f"{qid:{cw}}{e['type']:8}{e['information']:>7.3f}  {sep:>4}  {detail}")

    w("")
    dead = [q for q, e in report.items() if e["verdict"] == "DEAD"]
    weak = [q for q, e in report.items() if e["verdict"] == "weak"]
    blind = [q for q, e in report.items() if not e["separates"]]

    if dead:
        w(f"  DEAD ({len(dead)}): answer barely moves across these inputs -- "
          "costs tokens, tells you nothing here")
        for q in dead:
            w(f"      {q}")
    if weak:
        w(f"  weak ({len(weak)}): {', '.join(weak)}")
    if blind:
        w(f"  never separates ({len(blind)}): one side of the decision boundary only -- "
          "cannot gate anything on THIS input set")
        for q in blind:
            w(f"      {q}")
    if not dead and not blind:
        w("  every question varies with the input and reaches both sides of its boundary.")

    concentrated = [(q, e) for q, e in report.items() if e.get("positives_concentrated")]
    if concentrated:
        w("")
        w("  COVERAGE: every positive came from a single input class.")
        w("  This is EXPECTED when the question is about that class, and a red flag when it is")
        w("  not -- which only you can tell, because the distinction is semantic. Concentration")
        w("  is where to look, not a verdict.")
        for q, e in concentrated:
            grp = e.get("positive_groups", {})
            only = next(iter(grp), None)
            w(f"      {q:38} {grp.get(only, 0):>3} positive(s), all from {only!r}")
        w("")
        w("  The availability-screen case this was built for: a Noul fired only on rate-limited")
        w("  responses, but its premise was about a genuine absence, and a rate limit is not an")
        w("  absence -- so every positive was the wrong kind. The tool surfaces the pattern; a")
        w("  reader supplies the mismatch.")

    w("")
    w("  Low information is a finding, not a verdict. It means one of: the question is")
    w("  irrelevant to this input set, its wording collapses a distinction that matters,")
    w("  or it is genuinely dead weight. Only the third is a delete.")
    w("")
    w(f"  {calls} calls, ${cost:.6f}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure whether a question carries information.")
    ap.add_argument("--set", dest="set_name", required=True,
                    help=f"registered QuestionSet. Registered: {', '.join(names())}")
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--group-by", dest="group_by",
                    help="dotted path into each input's state (e.g. probe.http_status). "
                         "Groups each question's positives by that field, which surfaces "
                         "positives that are all one input class.")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    qs = get(args.set_name)
    inputs = load_inputs(pathlib.Path(args.inputs))
    if args.limit:
        inputs = inputs[: args.limit]
    if len(inputs) < 2:
        raise SystemExit("need at least 2 inputs -- information is variation ACROSS inputs")

    client = TypeSafeClient()

    def run(item):
        # allow_unvalidated: measuring a question set is not acting on its answers.
        return qs.ask(client, item["state"], allow_unvalidated=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, inputs))

    report = analyse(qs, results, inputs, args.group_by)
    cost = sum(r.cost_usd for r in results)
    print(render(qs, report, len(inputs), cost, len(results)))

    if args.json_out:
        pathlib.Path(args.json_out).write_text(
            json.dumps({"set": f"{qs.name}@{qs.version}", "n_inputs": len(inputs),
                        "questions": report}, indent=2) + "\n")
        print(f"  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
