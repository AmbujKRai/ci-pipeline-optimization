"""Experiment E3: how test wall time scales with the number of pytest-xdist workers.

Runs the whole suite serially and with 2, 4 and 8 workers (several repetitions each) and
writes the results to docs/results/local_parallelism.json.

    python scripts/benchmark_local.py --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "results" / "local_parallelism.json"


def run_suite(workers: int) -> float:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    command += ["-n", str(workers)] if workers else ["-p", "no:xdist"]
    if workers:
        command += ["--dist", "worksteal"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:], sep="\n")
        raise SystemExit(f"test run with {workers} workers failed")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, nargs="+", default=[0, 2, 4, 8])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    rows = []
    for workers in args.workers:
        label = "serial" if workers == 0 else f"{workers} workers"
        timings = []
        for attempt in range(1, args.repeats + 1):
            seconds = run_suite(workers)
            timings.append(round(seconds, 2))
            print(f"{label:>10}  run {attempt}: {seconds:6.1f} s", flush=True)
        median = statistics.median(timings)
        rows.append({"workers": workers, "label": label, "runs": timings, "median": median})

    baseline = rows[0]["median"]
    for row in rows:
        row["speedup"] = round(baseline / row["median"], 2)
        effective_workers = max(row["workers"], 1)
        row["efficiency"] = round(row["speedup"] / effective_workers, 2)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "E3 local parallelism",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "results": rows,
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n| Workers | Median wall time (s) | Speed-up | Efficiency |")
    print("|---|---|---|---|")
    for row in rows:
        print(f"| {row['label']} | {row['median']:.1f} | {row['speedup']}x | {row['efficiency']} |")
    print(f"\nSaved to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
