#!/usr/bin/env python3
"""confidence-accuracy-curve -- the artifact the docs say sets a threshold, and the one gap in
this repo's instruments this tool closes: we had never plotted confidence against accuracy.

`consistency_probe.py` measures whether an answer holds still across repeats. `question_health.py`
measures whether it varies across inputs. Neither says whether a HIGH-confidence answer is more
likely to be RIGHT than a low-confidence one -- and that is the one relationship a threshold
actually rests on (`knowledge/cookbooks-reliability.md` sections 3-4, `knowledge/MASTERY.md`
section 4: "confidence... is not correctness"; a threshold is a bet that it correlates with
correctness anyway, and that bet needs a plot, not an assumption).

WHAT IT MEASURES. One call per labelled case (repeats are the consistency probe's job; this tool
answers each case exactly once, like `question_health.py` does). For the one question you name
with `--answer`, it pairs every answer with the label's expected outcome and produces:

  1. RELIABILITY BINS  -- local (non-cumulative) accuracy inside each confidence band. This is
     the actual "confidence vs accuracy curve": does accuracy degrade as confidence drops, and
     where.
  2. CUT TABLE          -- cumulative "act automatically whenever confidence >= this cut": at
     every candidate cut, the automation rate (share of cases that clear it) against the error
     rate among the cases that do. This is the trade a threshold buys.
  3. BANDS              -- act / review / do-not-act, sized and scored, ONLY if you pass
     `--low`/`--high` yourself. This tool never invents a cutoff -- see `knowledge/MASTERY.md`
     section 9 and `cookbooks-reliability.md`'s repeated "illustrative, not calibrated" caveat.

PRIMITIVE-AWARE, NOT PRIMITIVE-BLIND. `confidence` exists only on Choice and Score
(`MASTERY.md` section 4). For a Noul, the equivalent signal is distance from 0.5 -- reported as
`noul_certainty` and never printed next to a real `confidence` as if they were the same unit
(section 9: a Choice and a Noul do not share a scale on identical evidence). Every report says
which one it used.

GENERIC BY DESIGN. Point it at any registered `QuestionSet` and any labelled-cases JSON file
(a list of `{"state": ..., "<expected-key>": ...}` records -- `examples/labels/ticket_triage.json`
is the shape). A Choice's raw picks usually live in a different vocabulary than the label's
expected values -- supply `--map` to translate, inline JSON or a `module:attr` dotted reference
to an existing dict. If the labelled state carries fields the question set doesn't read,
`--transform` narrows it before the call (inline `module:function`, or the default `identity`).

WHAT THIS DOES NOT DO. It does not pick a threshold, does not emit `validated_on` (same
discipline as `consistency_probe.py` -- that string names an accuracy check *and the data*, and
a single run against one labelled file is exactly that, but naming it is the calibrator's call,
not this tool's), and does not gate anything (`gates_irreversible=False` in spirit -- this is a
read-only report). Advisory only.

CLI:
    python3 tools/confidence_accuracy_curve.py --set ticket_triage \\
        --labels examples/labels/ticket_triage.json --answer team --expected expected_team

    # preview a candidate three-way split (does not commit anything):
    python3 tools/confidence_accuracy_curve.py --set ticket_triage \\
        --labels examples/labels/ticket_triage.json --answer team --expected expected_team \\
        --low 0.5 --high 0.9

Importable:
    import sys; sys.path.insert(0, "tools")   # or wherever this kit lives relative to you
    from confidence_accuracy_curve import evaluate, reliability_bins, cut_table, band_report
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from typesafe_client import TypeSafeClient  # noqa: E402
from questions import QuestionSet, get, names  # noqa: E402

DEFAULT_BINS = 5   # local reliability bins. Coarser than consistency_probe's 15 repeats on
                    # purpose -- with dozens, not thousands, of labelled cases, 10 bins mostly
                    # report n=1 noise. This is a report-readability choice, not a measurement.


# --- Loading -------------------------------------------------------------------------------


@dataclass
class LabelledCase:
    id: str
    state: Any
    expected: Any


def load_labelled(path: str | Path, expected_key: str = "expected") -> list[LabelledCase]:
    """A JSON list of `{"state": ..., "<expected_key>": ...}` records, e.g.
    `examples/labels/ticket_triage.json` with `expected_key="expected_team"`."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path}: expected a non-empty JSON list")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "state" not in item:
            raise SystemExit(f"{path}[{i}]: missing 'state'")
        if expected_key not in item:
            raise SystemExit(
                f"{path}[{i}]: missing {expected_key!r} (pass --expected to name the field "
                f"that carries ground truth; keys present: {sorted(item)})"
            )
        out.append(LabelledCase(id=str(item.get("id", f"case-{i}")), state=item["state"],
                                 expected=item[expected_key]))
    return out


def resolve_dotted(ref: str) -> Any:
    """`"module.path:attr"` -> the attribute. Used for both `--map` and `--transform` when they
    point at an existing dict/function rather than inline JSON."""
    if ":" not in ref:
        raise SystemExit(f"{ref!r} is not a module:attr reference")
    mod_name, attr = ref.split(":", 1)
    mod = importlib.import_module(mod_name)
    try:
        return getattr(mod, attr)
    except AttributeError:
        raise SystemExit(f"{mod_name!r} has no attribute {attr!r}") from None


def resolve_map(spec: str | None) -> Mapping[str, str] | None:
    if spec is None:
        return None
    if spec.strip().startswith("{"):
        return json.loads(spec)
    return dict(resolve_dotted(spec))


def resolve_transform(spec: str | None) -> Callable[[Any], Any]:
    if spec is None or spec == "identity":
        return lambda state: state
    fn = resolve_dotted(spec)
    return fn


# --- Judging ---------------------------------------------------------------------------------


@dataclass
class Judged:
    id: str
    expected: Any
    raw_answer: Any            # the choice label / score value / noul probability, unmapped
    predicted: Any             # after --map, if any; equals raw_answer for score/noul
    metric: float               # confidence (choice/score) or noul_certainty (noul)
    metric_kind: str            # "confidence" | "noul_certainty" -- never compare across kinds
    correct: bool
    cost_usd: float
    latency_ms: int
    error: str | None = None


def _parse_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x >= 0.5
    s = str(x).strip().lower()
    if s in ("yes", "true", "1"):
        return True
    if s in ("no", "false", "0"):
        return False
    raise ValueError(f"Cannot read {x!r} as a Noul-style boolean expectation")


def judge_one(
    qs: QuestionSet,
    client: TypeSafeClient,
    case: LabelledCase,
    answer_key: str,
    *,
    value_map: Mapping[str, str] | None,
    transform: Callable[[Any], Any],
) -> Judged:
    qtype = qs.questions[answer_key]["type"]
    try:
        state = transform(case.state)
        r = qs.ask(client, state, allow_unvalidated=True)
    except Exception as exc:  # noqa: BLE001 -- one bad case must not kill the batch
        return Judged(id=case.id, expected=case.expected, raw_answer=None, predicted=None,
                       metric=0.0, metric_kind="error", correct=False, cost_usd=0.0,
                       latency_ms=0, error=str(exc))

    if qtype == "choice":
        raw = r.choice(answer_key)
        predicted = value_map.get(raw, raw) if value_map else raw
        correct = predicted == case.expected
        metric, kind = r.confidence(answer_key), "confidence"
    elif qtype == "score":
        raw = r.score(answer_key)
        predicted = round(raw)
        correct = predicted == round(float(case.expected))
        metric, kind = r.confidence(answer_key), "confidence"
    elif qtype == "noul":
        raw = r.noul(answer_key)
        predicted = raw >= 0.5
        correct = predicted == _parse_bool(case.expected)
        metric, kind = abs(raw - 0.5) * 2.0, "noul_certainty"
    else:
        raise ValueError(f"Unknown question type {qtype!r}")

    return Judged(id=case.id, expected=case.expected, raw_answer=raw, predicted=predicted,
                   metric=round(metric, 4), metric_kind=kind, correct=correct,
                   cost_usd=r.cost_usd, latency_ms=r.latency_ms)


def evaluate(
    qs: QuestionSet,
    cases: list[LabelledCase],
    answer_key: str,
    *,
    value_map: Mapping[str, str] | None = None,
    transform: Callable[[Any], Any] | None = None,
    client: TypeSafeClient | None = None,
    workers: int = 5,
) -> list[Judged]:
    if answer_key not in qs.questions:
        raise KeyError(f"{qs.name!r} has no question {answer_key!r}. Has: {sorted(qs.questions)}")
    client = client or TypeSafeClient(model=qs.model)
    transform = transform or (lambda state: state)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(
            lambda c: judge_one(qs, client, c, answer_key, value_map=value_map,
                                 transform=transform),
            cases,
        ))


# --- Analysis ------------------------------------------------------------------------------


@dataclass
class BinStat:
    lo: float
    hi: float
    n: int
    accuracy: float | None       # None when n == 0 -- an empty bin has no accuracy to report


def reliability_bins(judged: list[Judged], n_bins: int = DEFAULT_BINS) -> list[BinStat]:
    """LOCAL accuracy inside each confidence band -- not cumulative. This is where 'find where
    accuracy degrades' (the calibrator's own method, step 3) actually gets answered: a cumulative
    cut table can hide a dip in the middle behind a large 'above cut' bucket that averages over
    it."""
    ok = [j for j in judged if j.error is None]
    edges = [i / n_bins for i in range(n_bins + 1)]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        in_bin = [j for j in ok if (lo <= j.metric <= hi if hi == 1.0 else lo <= j.metric < hi)]
        acc = (sum(j.correct for j in in_bin) / len(in_bin)) if in_bin else None
        out.append(BinStat(lo=round(lo, 2), hi=round(hi, 2), n=len(in_bin),
                            accuracy=round(acc, 3) if acc is not None else None))
    return out


@dataclass
class CutStat:
    cut: float
    n_at_or_above: int
    automation_rate: float       # share of ALL cases that clear this cut
    accuracy: float | None       # among those that clear it
    error_rate: float | None     # 1 - accuracy; None when n_at_or_above == 0


def cut_table(judged: list[Judged], cuts: list[float] | None = None) -> list[CutStat]:
    """Cumulative 'act automatically whenever metric >= cut': the automation-rate-vs-error-rate
    trade a threshold actually buys. Default candidate cuts are every distinct metric value
    observed (plus 0.0) -- the only points where the automation set actually changes; an
    evenly-spaced grid would print several rows with identical membership."""
    ok = [j for j in judged if j.error is None]
    if cuts is None:
        cuts = sorted({0.0} | {j.metric for j in ok})
    n = len(ok)
    out = []
    for c in cuts:
        above = [j for j in ok if j.metric >= c]
        acc = (sum(j.correct for j in above) / len(above)) if above else None
        out.append(CutStat(
            cut=round(c, 4), n_at_or_above=len(above),
            automation_rate=round(len(above) / n, 3) if n else 0.0,
            accuracy=round(acc, 3) if acc is not None else None,
            error_rate=round(1 - acc, 3) if acc is not None else None,
        ))
    return out


@dataclass
class BandStat:
    name: str
    n: int
    share: float
    accuracy: float | None


@dataclass
class BandsReport:
    low: float | None
    high: float | None
    bands: list[BandStat]
    note: str


def band_report(judged: list[Judged], low: float | None, high: float | None) -> BandsReport:
    """act / review / do-not-act, ONLY when the caller supplies both cuts. No default is
    invented here -- see the module docstring and MASTERY.md section 9."""
    ok = [j for j in judged if j.error is None]
    n = len(ok)
    if low is None or high is None:
        return BandsReport(low=low, high=high, bands=[],
                            note="pass --low and --high to preview a three-way split; this "
                                 "tool does not invent one")
    if low > high:
        raise ValueError(f"--low ({low}) must be <= --high ({high})")

    def stat(name: str, members: list[Judged]) -> BandStat:
        acc = (sum(j.correct for j in members) / len(members)) if members else None
        return BandStat(name=name, n=len(members),
                         share=round(len(members) / n, 3) if n else 0.0,
                         accuracy=round(acc, 3) if acc is not None else None)

    do_not_act = [j for j in ok if j.metric < low]
    review = [j for j in ok if low <= j.metric < high]
    act = [j for j in ok if j.metric >= high]
    return BandsReport(
        low=low, high=high,
        bands=[stat("act", act), stat("review", review), stat("do_not_act", do_not_act)],
        note="do_not_act's accuracy is informational only (what it WOULD score if forced to "
             "act) -- nothing acts on that band by definition.",
    )


# --- CLI -------------------------------------------------------------------------------------


def _print_human(qs, answer_key, judged, bins, cuts, bands) -> None:
    ok = [j for j in judged if j.error is None]
    errs = [j for j in judged if j.error is not None]
    kind = ok[0].metric_kind if ok else "?"
    overall_acc = sum(j.correct for j in ok) / len(ok) if ok else 0.0

    print(f"CONFIDENCE-ACCURACY CURVE   {qs.name}@{qs.version}  question={answer_key}  "
          f"metric={kind}")
    print(f"CASES           {len(judged)} labelled ({len(ok)} scored, {len(errs)} errored)")
    print(f"OVERALL         accuracy {overall_acc:.3f} ({sum(j.correct for j in ok)}/{len(ok)})")
    if errs:
        print(f"  ERRORS ({len(errs)}): " + "; ".join(f"{e.id}: {e.error}" for e in errs[:3]))
    print()

    print(f"RELIABILITY  -- local accuracy inside each {kind} band (not cumulative)")
    print(f"  {'band':<14}{'n':>5}{'accuracy':>10}")
    for b in bins:
        acc = f"{b.accuracy:.3f}" if b.accuracy is not None else "n/a"
        print(f"  [{b.lo:.2f}, {b.hi:.2f}]".ljust(16) + f"{b.n:>5}{acc:>10}")
    print()

    print(f"CUT TABLE   -- act automatically whenever {kind} >= cut")
    print(f"  {'cut':>6}{'n>=cut':>8}{'automation':>12}{'accuracy':>10}{'error rate':>12}")
    for c in cuts:
        acc = f"{c.accuracy:.3f}" if c.accuracy is not None else "n/a"
        err = f"{c.error_rate:.3f}" if c.error_rate is not None else "n/a"
        print(f"  {c.cut:>6.3f}{c.n_at_or_above:>8}{c.automation_rate:>12.3f}{acc:>10}{err:>12}")
    print()

    print("BANDS")
    if not bands.bands:
        print(f"  {bands.note}")
    else:
        print(f"  low={bands.low}  high={bands.high}")
        for b in bands.bands:
            acc = f"{b.accuracy:.3f}" if b.accuracy is not None else "n/a"
            print(f"  {b.name:<12}{b.n:>5} ({b.share:.1%})   accuracy={acc}")
        print(f"  {bands.note}")
    print()
    print("This tool does not emit validated_on. That string names an accuracy check AND the")
    print("data behind it -- naming it is the calibrator's decision after reading this report,")
    print("not something a single run should assert for itself.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="confidence_accuracy_curve.py",
        description="Plot confidence against accuracy for a QuestionSet against labelled cases: "
                     "reliability bins, a cut table (automation rate vs error rate), and an "
                     "optional act/review/do-not-act band preview.",
    )
    ap.add_argument("--set", dest="set_name", required=True,
                     help=f"Registered QuestionSet. Registered: {', '.join(names())}")
    ap.add_argument("--labels", required=True, help="JSON list of labelled cases")
    ap.add_argument("--answer", required=True, help="question id to score (its confidence/noul "
                                                      "is the x-axis)")
    ap.add_argument("--expected", default="expected", help="field in each label record holding "
                                                             "ground truth (default: 'expected')")
    ap.add_argument("--map", help='Choice-answer -> expected-vocabulary translation: inline '
                                   'JSON ({"a":"b"}) or a module:attr dotted reference to an '
                                   'existing dict')
    ap.add_argument("--transform", help="narrow/reshape labelled state before the call: "
                                         "'identity' (default) or a module:function reference")
    ap.add_argument("--bins", type=int, default=DEFAULT_BINS)
    ap.add_argument("--low", type=float, default=None)
    ap.add_argument("--high", type=float, default=None)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--json-out", dest="json_out")
    args = ap.parse_args(argv)

    try:
        qs = get(args.set_name)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cases = load_labelled(args.labels, expected_key=args.expected)
    if args.limit:
        cases = cases[: args.limit]

    value_map = resolve_map(args.map)
    transform = resolve_transform(args.transform)

    judged = evaluate(qs, cases, args.answer, value_map=value_map, transform=transform,
                       workers=args.workers)
    bins = reliability_bins(judged, n_bins=args.bins)
    cuts = cut_table(judged)
    bands = band_report(judged, args.low, args.high)

    if args.as_json or args.json_out:
        payload = {
            "set": f"{qs.name}@{qs.version}", "answer": args.answer,
            "judged": [asdict(j) for j in judged],
            "reliability_bins": [asdict(b) for b in bins],
            "cut_table": [asdict(c) for c in cuts],
            "bands": asdict(bands),
        }
        text = json.dumps(payload, indent=2)
        if args.json_out:
            Path(args.json_out).write_text(text + "\n")
            print(f"wrote {args.json_out}", file=sys.stderr)
        if args.as_json:
            print(text)
        return 0

    _print_human(qs, args.answer, judged, bins, cuts, bands)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
