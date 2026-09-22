#!/usr/bin/env python3
"""consistency-probe -- measure TypeSafe self-consistency empirically, not by assumption.

Points at a registered `QuestionSet` and a set of inputs, repeats each input's request N
times, and reports how much the answer moves run to run. This is the measurement the
cookbooks warn everyone skips (`knowledge/cookbooks-reliability.md` sections 3-5,
`knowledge/MASTERY.md` section 4, 9): "temperature=0" is not determinism, a stable label can
sit on top of an unstable probability vector, and a Noul's scalar behaves differently from a
Choice/Score's distribution because it answers a different kind of question ("is this true at
all" vs "which option wins").

What it measures, per question, per input, then aggregated:

  - **Choice / Score** -- the full probability distribution's spread: for each option/level,
    the std dev of that option's probability across the N repeats, averaged over
    options/levels. This is the same statistic the cookbooks call "mean per-question
    probability std dev" -- never just whether the top label/level agreed.
  - **Noul** -- has no distribution and no `confidence` (MASTERY.md section 4: "Returned on
    Choice and Score only"). Its instability is the std dev of its own scalar across repeats
    -- the same statistic family, applied to the one number a Noul gives you.
  - Both primitives also get **agreement%**: how often the modal pick (Choice's own label;
    argmax(probabilities) for Score; which side of 0.5 for Noul) repeats. Reported *next to*
    the spread number on purpose -- the cookbook's own case was 90.8% label agreement while
    two questions' probabilities were oscillating between two labels underneath it, so
    agreement% alone is not the signal to trust.

What it deliberately does NOT do: pick an "uncertain band" cutoff, decide whether a question
is "stable enough", or emit a `validated_on` string. See VALIDATED_ON_NOTE below for why --
short version: self-consistency is not accuracy, and pretending it is would let a question
that is perfectly stable and perfectly wrong pass as validated.

CLI:
    python3 tools/consistency_probe.py --set ticket_triage --inputs inputs.json
    python3 tools/consistency_probe.py --set ticket_triage --inputs inputs.json \
        --repeats 15 --json

`inputs.json` is either a plain list of states:
    [{"ticket": "..."}, {"ticket": "..."}]
or a list with explicit ids:
    [{"id": "t1", "state": {"ticket": "..."}}, ...]

Importable:
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT / "tools"))
    from consistency_probe import probe, estimate_run, load_inputs
    from questions import get

    report = probe(get("ticket_triage"), load_inputs("inputs.json"), repeats=15)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from typesafe_client import (  # noqa: E402
    USD_PER_INPUT_TOKEN,
    Result,
    TypeSafeClient,
    estimate_tokens,
)
from questions import QuestionSet, get, names  # noqa: E402

TAG = "consistency-probe"
DEFAULT_REPEATS = 15

#: Above either of these, the CLI refuses to run without --yes. The API is cheap per call
#: ($0.042/M input tokens), so call *count* -- not cost -- is the number worth gating on; the
#: cost figure is included mainly so a genuinely large batch doesn't run unannounced either.
WARN_CALLS = 50
WARN_COST_USD = 0.05

VALIDATED_ON_NOTE = (
    "This tool never emits a validated_on string, and no wrapper around it should. "
    "Self-consistency measures whether an answer holds still across repeats, not whether it "
    "is correct -- a question can be perfectly stable and perfectly wrong (MASTERY.md section "
    "9's Choice/Noul divergence is exactly this: both are confident, only one scale means what "
    "you think it means). validated_on must name an accuracy check against labelled ground "
    "truth (typesafe-calibrator's job). What this report IS good for: deciding which questions "
    "are even worth spending labelling effort on -- rank by instability below, and send "
    "anything near the top back to typesafe-question-smith before calibrating it."
)


# --- Data shapes -----------------------------------------------------------------------------


@dataclass
class QuestionStability:
    """Stats for one question, over N repeats (either on one input, or averaged across
    several -- `_aggregate` produces the latter)."""

    qid: str
    qtype: str
    n: int
    agreement_pct: float          # how often the modal pick repeated
    modal_pick: str
    vector_std: float | None      # mean per-option/level probability std dev; None for noul
    scalar_mean: float            # choice: mean top-probability; score: mean normalized score;
                                   # noul: mean of the raw value
    scalar_std: float             # std dev of the same scalar -- the ONLY spread a noul has
    confidence_mean: float | None
    confidence_std: float | None

    @property
    def instability(self) -> float:
        """The number used for ranking. Choice/Score rank on vector_std (the same statistic
        the cookbooks use); Noul has no vector, so it ranks on its own scalar_std -- the same
        statistic family, not an invented substitute. Do not compare this number *across*
        primitive types as if it meant the same thing (MASTERY.md section 9: a Choice and a
        Noul do not share a scale even on identical evidence) -- it is safe to sort by, it is
        not safe to threshold on without calibration."""
        return self.vector_std if self.vector_std is not None else self.scalar_std


@dataclass
class InputReport:
    input_id: str
    questions: dict[str, QuestionStability]


@dataclass
class ProbeReport:
    question_set: str
    version: str
    model: str
    repeats: int
    n_inputs: int
    total_calls: int
    inputs: list[InputReport]
    aggregate: dict[str, QuestionStability]
    total_input_tokens: int
    total_cost_usd: float
    total_latency_ms: int          # sum of individual call latencies (the real spend driver)
    wall_clock_s: float            # actual elapsed time -- shrinks with concurrency, cost doesn't
    note_on_validated_on: str = VALIDATED_ON_NOTE


# --- Inputs ------------------------------------------------------------------------------


def load_inputs(path: str | Path) -> list[tuple[str, Any]]:
    """Parse an inputs JSON file into `(input_id, state)` pairs."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON list of inputs")
    out: list[tuple[str, Any]] = []
    for i, item in enumerate(data):
        if isinstance(item, dict) and "state" in item:
            out.append((str(item.get("id", f"input-{i}")), item["state"]))
        else:
            out.append((f"input-{i}", item))
    return out


def estimate_run(question_set: QuestionSet, inputs: list[tuple[str, Any]], repeats: int) -> dict:
    """Pre-flight estimate before spending anything. `total_calls` is exact (repeats scale
    spend linearly -- this is the whole point of the tool's cost warning: N repeats means N
    times the spend of one call, always). Token/cost/time are rough, using the same estimator
    the client itself uses for its own budget warning."""
    total_calls = len(inputs) * repeats
    token_estimates = [
        estimate_tokens({"state": state, "questions": question_set.questions})
        for _, state in inputs
    ] or [0]
    est_tokens_per_call = statistics.fmean(token_estimates)
    return {
        "total_calls": total_calls,
        "est_tokens_per_call": round(est_tokens_per_call),
        "est_cost_usd": round(est_tokens_per_call * total_calls * USD_PER_INPUT_TOKEN, 6),
        "est_seconds_serial": round(total_calls * 0.4, 1),  # ~observed mean call time, cookbooks
    }


# --- Probe -------------------------------------------------------------------------------


def probe(
    question_set: QuestionSet,
    inputs: list[tuple[str, Any]],
    *,
    repeats: int = DEFAULT_REPEATS,
    client: TypeSafeClient | None = None,
    concurrency: int = 5,
) -> ProbeReport:
    """Repeat `question_set` against every input `repeats` times and measure the spread.

    Tags every call `"consistency-probe"` regardless of the set's own name -- this is a
    measurement pass, not the set's normal traffic, and usage-log attribution should say so.
    Calls the client directly rather than `question_set.ask()` for the same reason: a probe
    run is explicitly exempt from the gates_irreversible/validated_on check (it is how you'd
    generate evidence for that check in the first place, not an action the check protects).
    """
    client = client or TypeSafeClient(model=question_set.model)
    started = time.monotonic()
    all_calls: list[Result] = []

    def _call(state: Any) -> Result:
        return client.ask(state=state, questions=question_set.questions,
                           model=question_set.model, tag=TAG)

    input_reports: list[InputReport] = []
    for input_id, state in inputs:
        results: list[Result | None] = [None] * repeats
        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(_call, state): i for i in range(repeats)}
                for fut in as_completed(futures):
                    results[futures[fut]] = fut.result()
        else:
            for i in range(repeats):
                results[i] = _call(state)

        results = [r for r in results if r is not None]
        all_calls.extend(results)
        repeats_by_qid = _collect_repeats(question_set, results)
        input_reports.append(InputReport(input_id=input_id, questions=_stabilities(repeats_by_qid)))

    wall_clock_s = round(time.monotonic() - started, 2)

    return ProbeReport(
        question_set=question_set.name,
        version=question_set.version,
        model=question_set.model,
        repeats=repeats,
        n_inputs=len(inputs),
        total_calls=len(all_calls),
        inputs=input_reports,
        aggregate=_aggregate(input_reports),
        total_input_tokens=sum(c.input_tokens for c in all_calls),
        total_cost_usd=sum(c.cost_usd for c in all_calls),
        total_latency_ms=sum(c.latency_ms for c in all_calls),
        wall_clock_s=wall_clock_s,
    )


@dataclass
class _Repeats:
    qid: str
    qtype: str
    picked: list[str]
    probabilities: list[dict[str, float]] | None
    confidence: list[float] | None
    scalar: list[float]


def _collect_repeats(qs: QuestionSet, results: list[Result]) -> dict[str, _Repeats]:
    """Pull the primitive-appropriate numbers out of N raw `Result`s, per question."""
    out: dict[str, _Repeats] = {}
    for qid, qdef in qs.questions.items():
        qtype = qdef["type"]
        picked: list[str] = []
        probs: list[dict[str, float]] = []
        confs: list[float] = []
        scalars: list[float] = []
        for r in results:
            if qtype == "choice":
                picked.append(r.choice(qid))
                p = r.probabilities(qid)
                probs.append(p)
                confs.append(r.confidence(qid))
                scalars.append(max(p.values()))
            elif qtype == "score":
                p = r.probabilities(qid)
                probs.append(p)
                confs.append(r.confidence(qid))
                picked.append(max(p, key=p.get))
                scalars.append(r.normalized_score(qid))
            elif qtype == "noul":
                v = r.noul(qid)
                # 0.5 is the primitive's own defined midpoint ("yes and no equally likely"),
                # used here only to describe which side each repeat landed on -- it is NOT a
                # recommended action threshold. Never treat this split as calibrated.
                picked.append("yes" if v > 0.5 else "no")
                scalars.append(v)
            else:
                raise ValueError(f"Unknown question type {qtype!r} for {qid!r}")
        out[qid] = _Repeats(
            qid=qid, qtype=qtype, picked=picked,
            probabilities=probs if qtype != "noul" else None,
            confidence=confs if qtype != "noul" else None,
            scalar=scalars,
        )
    return out


def _stdev(xs: list[float]) -> float:
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def _vector_std(probs: list[dict[str, float]]) -> float:
    """Mean, over every option/level, of that option's std dev across repeats. The same
    "mean per-question probability std dev" the cookbooks report."""
    if not probs:
        return 0.0
    per_key = [_stdev([p.get(k, 0.0) for p in probs]) for k in probs[0]]
    return statistics.fmean(per_key) if per_key else 0.0


def _mode_pct(picks: list[str]) -> tuple[str, float]:
    label, n = Counter(picks).most_common(1)[0]
    return label, 100.0 * n / len(picks)


def _stabilities(reps_by_qid: dict[str, _Repeats]) -> dict[str, QuestionStability]:
    out: dict[str, QuestionStability] = {}
    for qid, r in reps_by_qid.items():
        modal, agreement = _mode_pct(r.picked)
        vector_std = _vector_std(r.probabilities) if r.probabilities is not None else None
        conf_mean = statistics.fmean(r.confidence) if r.confidence else None
        conf_std = _stdev(r.confidence) if r.confidence else None
        out[qid] = QuestionStability(
            qid=qid, qtype=r.qtype, n=len(r.picked),
            agreement_pct=round(agreement, 1), modal_pick=modal,
            vector_std=round(vector_std, 4) if vector_std is not None else None,
            scalar_mean=round(statistics.fmean(r.scalar), 4),
            scalar_std=round(_stdev(r.scalar), 4),
            confidence_mean=round(conf_mean, 4) if conf_mean is not None else None,
            confidence_std=round(conf_std, 4) if conf_std is not None else None,
        )
    return out


def _aggregate(input_reports: list[InputReport]) -> dict[str, QuestionStability]:
    """Average each question's stats across inputs. Equal-weighted per input (every input
    contributes the same `repeats`, so this is also equal-weighted per repeat)."""
    by_qid: dict[str, list[QuestionStability]] = {}
    for ir in input_reports:
        for qid, stab in ir.questions.items():
            by_qid.setdefault(qid, []).append(stab)

    out: dict[str, QuestionStability] = {}
    for qid, stats in by_qid.items():
        vector_stds = [s.vector_std for s in stats if s.vector_std is not None]
        conf_means = [s.confidence_mean for s in stats if s.confidence_mean is not None]
        conf_stds = [s.confidence_std for s in stats if s.confidence_std is not None]
        out[qid] = QuestionStability(
            qid=qid, qtype=stats[0].qtype, n=sum(s.n for s in stats),
            agreement_pct=round(statistics.fmean(s.agreement_pct for s in stats), 1),
            modal_pick=stats[0].modal_pick if len(stats) == 1 else "(varies by input)",
            vector_std=round(statistics.fmean(vector_stds), 4) if vector_stds else None,
            scalar_mean=round(statistics.fmean(s.scalar_mean for s in stats), 4),
            scalar_std=round(statistics.fmean(s.scalar_std for s in stats), 4),
            confidence_mean=round(statistics.fmean(conf_means), 4) if conf_means else None,
            confidence_std=round(statistics.fmean(conf_stds), 4) if conf_stds else None,
        )
    return out


# --- CLI ---------------------------------------------------------------------------------


def _print_human(report: ProbeReport) -> None:
    print(f"QUESTION SET    {report.question_set}@{report.version}  model={report.model}")
    print(f"REPEATS         {report.repeats} x {report.n_inputs} input(s) = "
          f"{report.total_calls} calls")
    print(f"WALL CLOCK      {report.wall_clock_s}s   "
          f"(sum of individual call latencies: {report.total_latency_ms}ms)")
    print()
    print("AGGREGATE -- ranked least stable first. instability = mean per-option/level")
    print("probability std dev for Choice/Score, or std dev of the raw value for Noul (a")
    print("Noul has no distribution). Same statistic family; do not compare the magnitude")
    print("across primitive types as if it meant the same thing -- see MASTERY.md section 9.")
    print()
    ranked = sorted(report.aggregate.values(), key=lambda s: s.instability, reverse=True)
    # Size the id column to the longest name present. A fixed width silently ran the id into
    # the type column for long ids ("mode7_criteria_conflictnoul"), which is worst precisely
    # for the descriptive names that make a report readable.
    w = max([len(s.qid) for s in ranked] + [len("qid")]) + 2
    print(f"{'qid':<{w}}{'type':<8}{'agree%':>8}{'instability':>13}{'conf mean':>11}{'conf std':>10}")
    for s in ranked:
        cm = f"{s.confidence_mean:.3f}" if s.confidence_mean is not None else "n/a"
        cs = f"{s.confidence_std:.3f}" if s.confidence_std is not None else "n/a"
        print(f"{s.qid:<{w}}{s.qtype:<8}{s.agreement_pct:>7.1f}%{s.instability:>13.4f}"
              f"{cm:>11}{cs:>10}")
    print()
    print(f"COST            ${report.total_cost_usd:.6f} total, "
          f"${report.total_cost_usd / max(report.total_calls, 1):.8f}/call, "
          f"{report.total_input_tokens} input tokens")
    print(f"LATENCY         {report.total_latency_ms}ms summed over {report.total_calls} "
          f"calls ({report.total_latency_ms / max(report.total_calls, 1):.0f}ms mean); "
          f"{report.wall_clock_s}s actual wall clock (concurrency recovers wall clock, "
          "never the token/call cost -- MASTERY.md section 7)")
    print()
    print("NOTE ON validated_on")
    for line in _wrap(report.note_on_validated_on, 96):
        print(f"  {line}")


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + 1 + len(w) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consistency_probe.py",
        description="Measure TypeSafe self-consistency empirically: repeat identical calls "
                     "N times per input and report the spread, not just whether the label "
                     "agreed.",
    )
    parser.add_argument("--set", required=True, dest="set_name",
                         help=f"Registered QuestionSet name. Registered: "
                              f"{', '.join(names()) or '(none)'}")
    parser.add_argument("--inputs", required=True,
                         help='JSON file: a list of states, or '
                              '[{"id": "...", "state": {...}}, ...]')
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--yes", action="store_true",
                         help=f"Skip the confirmation required above {WARN_CALLS} calls or "
                              f"${WARN_COST_USD:.2f} estimated cost.")
    args = parser.parse_args(argv)

    try:
        qs = get(args.set_name)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    inputs = load_inputs(args.inputs)
    est = estimate_run(qs, inputs, args.repeats)
    print(f"~{est['total_calls']} calls, ~${est['est_cost_usd']:.6f} estimated, "
          f"~{est['est_seconds_serial']}s if run serially "
          f"(this run uses concurrency={args.concurrency})", file=sys.stderr)

    if not args.yes and (est["total_calls"] > WARN_CALLS or est["est_cost_usd"] > WARN_COST_USD):
        print(f"Exceeds the default warn threshold ({WARN_CALLS} calls / "
              f"${WARN_COST_USD:.2f}). Re-run with --yes to proceed.", file=sys.stderr)
        return 3

    report = probe(qs, inputs, repeats=args.repeats, concurrency=args.concurrency)

    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
