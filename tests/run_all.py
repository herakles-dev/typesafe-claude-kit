#!/usr/bin/env python3
"""Aggregate runner for the TypeSafe suites.

Every suite in `tests/` is a standalone script, not a pytest module: it counts its
own assertions, prints `N passed, M failed`, and exits 1 on failure. `pytest tests/`
does not merely fail here -- it INTERNALERRORs during collection, because the suites
call `sys.exit()` at module scope and pytest executes that at import time.

So this runs each suite as a subprocess and aggregates. It deliberately does not
modify, import, or monkeypatch the suites: they stay exactly as they run by hand.

Run:  python3 tests/run_all.py [--only PAT] [--timeout SEC] [--list] [--json]

Most suites make live API calls and need TYPESAFE_API_KEY in the environment. Spend is
reported by diffing the usage log.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
USAGE_LOG = Path(
    os.environ.get("TYPESAFE_USAGE_LOG", str(Path.home() / ".typesafe" / "usage.jsonl"))
)

SUMMARY_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")


def discover(pattern=None):
    files = sorted(p for p in TESTS_DIR.glob("test_*.py") if p.is_file())
    if pattern:
        files = [p for p in files if pattern in p.name]
    return files


def usage_snapshot():
    """(row_count, total_cost_usd) -- tolerant of a missing or partial log."""
    if not USAGE_LOG.exists():
        return 0, 0.0
    rows, cost = 0, 0.0
    with USAGE_LOG.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                cost += float(json.loads(line).get("cost_usd", 0) or 0)
            except (ValueError, AttributeError):
                pass  # a torn line should not break reporting
    return rows, cost


def run_suite(path, timeout):
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO),
        )
        out, err, code = proc.stdout, proc.stderr, proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code, timed_out = None, True

    elapsed = time.time() - started
    matches = SUMMARY_RE.findall(out)
    passed, failed = (int(matches[-1][0]), int(matches[-1][1])) if matches else (0, 0)

    if timed_out:
        status = "TIMEOUT"
    elif not matches:
        status = "ERROR"          # died before reporting -- import error, crash, no key
    elif failed:
        status = "FAIL"
    elif code != 0:
        status = "INCONSISTENT"   # claims 0 failed but exited nonzero
    else:
        status = "PASS"

    tail = "\n".join([ln for ln in (err or out).strip().splitlines() if ln.strip()][-4:])
    return {
        "suite": path.name, "status": status, "passed": passed, "failed": failed,
        "exit_code": code, "seconds": round(elapsed, 1),
        "detail": "" if status in ("PASS", "FAIL") else tail,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="substring filter on suite filename")
    ap.add_argument("--timeout", type=int, default=600, help="per-suite seconds (default 600)")
    ap.add_argument("--list", action="store_true", help="list suites and exit, no run")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    suites = discover(args.only)
    if not suites:
        print(f"no suites matched {args.only!r} in {TESTS_DIR}", file=sys.stderr)
        return 2
    if args.list:
        for p in suites:
            print(p.name)
        return 0

    rows_before, cost_before = usage_snapshot()
    started = time.time()
    results = []
    for path in suites:
        if not args.json:
            print(f"  ... {path.name}", flush=True)
        results.append(run_suite(path, args.timeout))
    wall = time.time() - started
    rows_after, cost_after = usage_snapshot()

    tot_pass = sum(r["passed"] for r in results)
    tot_fail = sum(r["failed"] for r in results)
    broken = [r for r in results if r["status"] in ("ERROR", "TIMEOUT", "INCONSISTENT")]
    ok = not tot_fail and not broken

    summary = {
        "suites": len(results), "assertions": tot_pass + tot_fail,
        "passed": tot_pass, "failed": tot_fail,
        "broken_suites": [r["suite"] for r in broken],
        "seconds": round(wall, 1),
        "api_calls": rows_after - rows_before,
        "cost_usd": round(cost_after - cost_before, 6),
        "ok": ok, "results": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if ok else 1

    print(f"\n{'=' * 74}")
    print(f"  {'SUITE':<36} {'STATUS':<13} {'PASS':>5} {'FAIL':>5} {'SEC':>6}")
    print(f"  {'-' * 70}")
    for r in results:
        print(f"  {r['suite']:<36} {r['status']:<13} {r['passed']:>5} {r['failed']:>5} {r['seconds']:>6}")
        if r["detail"]:
            for ln in r["detail"].splitlines():
                print(f"        | {ln[:78]}")
    print(f"  {'-' * 70}")
    print(f"  {len(results)} suites, {tot_pass + tot_fail} assertions: "
          f"{tot_pass} passed, {tot_fail} failed"
          + (f", {len(broken)} suite(s) BROKEN" if broken else ""))
    print(f"  {wall:.1f}s wall, {rows_after - rows_before} API calls, "
          f"${cost_after - cost_before:.6f}")
    print(f"{'=' * 74}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
