"""Run the CI experiments by dispatching workflow runs one after another.

    python scripts/run_experiments.py e1 --repeats 5   # 2x2: orchestration x caching
    python scripts/run_experiments.py e2 --repeats 3   # shard scaling 1-8
    python scripts/run_experiments.py e4               # Docker build caching

Configurations are interleaved round-robin so that slow and fast periods on the shared
runners affect every configuration equally. Needs the GitHub CLI logged in with the
`workflow` scope. Every dispatched run is appended to docs/results/experiment-log.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "docs" / "results" / "experiment-log.json"

E1 = [
    ("ci-baseline.yml", {"enable_cache": "false"}),
    ("ci-baseline.yml", {"enable_cache": "true"}),
    ("ci-optimized.yml", {"enable_cache": "false", "shards": "4"}),
    ("ci-optimized.yml", {"enable_cache": "true", "shards": "4"}),
]
E2 = [("ci-optimized.yml", {"enable_cache": "true", "shards": str(n)}) for n in (1, 2, 4, 6, 8)]
E4 = [("docker-cache-benchmark.yml", {"repeats": "3"})]


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def dispatch(workflow: str, inputs: dict[str, str], label: str | None) -> int:
    started = datetime.now(UTC)
    fields = [arg for key, value in inputs.items() for arg in ("-f", f"{key}={value}")]
    if label and workflow.startswith("ci-"):
        fields += ["-f", f"label={label}"]
    gh("workflow", "run", workflow, "--ref", "main", *fields)
    for _ in range(60):
        time.sleep(5)
        listing = json.loads(
            gh(
                "run", "list", "--workflow", workflow, "--event", "workflow_dispatch",
                "--limit", "10", "--json", "databaseId,createdAt",
            )
        )  # fmt: skip
        fresh = [
            run
            for run in listing
            if datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00")) >= started
        ]
        if fresh:
            return min(fresh, key=lambda run: run["createdAt"])["databaseId"]
    raise RuntimeError(f"dispatched {workflow} but the run never appeared")


def wait(run_id: int) -> str:
    subprocess.run(
        ["gh", "run", "watch", str(run_id), "--interval", "20", "--exit-status"],
        cwd=ROOT,
        capture_output=True,
    )
    return gh("run", "view", str(run_id), "--json", "conclusion", "--jq", ".conclusion")


def record(entry: dict[str, object]) -> None:
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    log.append(entry)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def run_one(experiment: str, workflow: str, inputs: dict[str, str], label: str) -> None:
    for attempt in (1, 2):
        run_id = dispatch(workflow, inputs, label)
        print(f"{experiment}: {workflow} {inputs} -> run {run_id}", flush=True)
        conclusion = wait(run_id)
        record(
            {
                "experiment": experiment,
                "label": label,
                "workflow": workflow,
                "inputs": inputs,
                "run_id": run_id,
                "conclusion": conclusion,
                "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        print(f"   {conclusion}", flush=True)
        if conclusion != "cancelled" or attempt == 2:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch the CI experiments")
    parser.add_argument("experiment", choices=["e1", "e2", "e4"])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    if args.experiment == "e4":
        run_one("e4", *E4[0], label="e4")
        return 0

    plan = E1 if args.experiment == "e1" else E2
    # One warm-up run fills the virtualenv and Docker caches; it is excluded from results.
    run_one(args.experiment, "ci-optimized.yml", {"enable_cache": "true"}, f"{args.experiment}-warmup")
    for repeat in range(1, args.repeats + 1):
        for workflow, inputs in plan:
            run_one(args.experiment, workflow, inputs, f"{args.experiment}-r{repeat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
