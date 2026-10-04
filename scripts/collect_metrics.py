"""Collect CI timings from the GitHub REST API.

For every completed, successful run of the baseline and optimized CI workflows it records
the wall-clock time, the runner time (sum of all jobs, i.e. what you are billed for) and the
time spent in each stage. Runs already in the cache file are not fetched again.

    GITHUB_TOKEN=... python scripts/collect_metrics.py --repo owner/name \
        --cache .metrics-cache/runs.json --out site/data/metrics.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://api.github.com"
WORKFLOWS = {"ci-baseline.yml": "baseline", "ci-optimized.yml": "optimized"}

# Step names (from the workflow files) that make up each stage.
STAGES = {
    "setup": ("Set up Python environment",),
    "static": ("Lint (ruff)", "Security scan (bandit)"),
    "tests": ("Run test shard", "Run all tests (serial)"),
    "docker": ("Build and smoke-test image",),
}
TITLE_PATTERN = re.compile(
    r"cache=(?P<cache>true|false)(?:\s*·\s*shards=(?P<shards>\d+))?(?:.*\[(?P<label>[\w-]+)\])?"
)


def api_get(path: str, token: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    # Only api.github.com is ever requested.
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        return json.load(response)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds_between(start: str | None, end: str | None) -> float:
    begin, finish = parse_time(start), parse_time(end)
    if begin is None or finish is None:
        return 0.0
    return max(0.0, (finish - begin).total_seconds())


def classify(pipeline: str, title: str) -> dict[str, Any]:
    match = TITLE_PATTERN.search(title)
    cache = match.group("cache") == "true" if match else pipeline == "optimized"
    shards = int(match.group("shards")) if match and match.group("shards") else 0
    if pipeline == "optimized" and shards == 0:
        shards = 4
    label = match.group("label") if match else None
    key = f"{pipeline}|cache={'on' if cache else 'off'}"
    if pipeline == "optimized":
        key += f"|shards={shards}"
    return {"cache": cache, "shards": shards, "label": label, "config": key}


def summarise_run(run: dict[str, Any], pipeline: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    job_records = []
    for job in jobs:
        steps = [
            {
                "name": step["name"],
                "seconds": seconds_between(step.get("started_at"), step.get("completed_at")),
                "conclusion": step.get("conclusion"),
            }
            for step in job.get("steps", [])
        ]
        job_records.append(
            {
                "name": job["name"],
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "seconds": seconds_between(job.get("started_at"), job.get("completed_at")),
                "steps": steps,
            }
        )

    starts = [job["started_at"] for job in job_records if job["started_at"]]
    ends = [job["completed_at"] for job in job_records if job["completed_at"]]
    first_start, last_end = min(starts), max(ends)

    # Stage time on the critical path = the slowest job's time for that stage.
    stages: dict[str, float] = {}
    stages_summed: dict[str, float] = {}
    for stage, names in STAGES.items():
        per_job = [
            sum(step["seconds"] for step in job["steps"] if step["name"] in names)
            for job in job_records
        ]
        stages[stage] = round(max(per_job, default=0.0), 1)
        stages_summed[stage] = round(sum(per_job), 1)

    return {
        "id": run["id"],
        "pipeline": pipeline,
        "title": run.get("display_title", ""),
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "sha": run.get("head_sha", "")[:7],
        "url": run.get("html_url"),
        "created_at": run.get("created_at"),
        **classify(pipeline, run.get("display_title", "")),
        "wall_seconds": round(seconds_between(first_start, last_end), 1),
        "queue_and_wall_seconds": round(seconds_between(run.get("run_started_at"), last_end), 1),
        "runner_seconds": round(sum(job["seconds"] for job in job_records), 1),
        "jobs": len(job_records),
        "stages": stages,
        "stages_summed": stages_summed,
    }


def fetch_runs(repo: str, token: str, workflow: str, limit: int) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while len(runs) < limit:
        batch = api_get(
            f"/repos/{repo}/actions/workflows/{workflow}/runs",
            token,
            {"status": "completed", "per_page": 100, "page": page},
        )["workflow_runs"]
        if not batch:
            break
        runs.extend(batch)
        page += 1
    return runs[:limit]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("label", "") and record["label"].endswith("warmup"):
            continue  # warm-up runs only fill the caches
        groups.setdefault(record["config"], []).append(record)

    summary = {}
    for config, items in sorted(groups.items()):
        walls = [item["wall_seconds"] for item in items]
        runner = [item["runner_seconds"] for item in items]
        summary[config] = {
            "runs": len(items),
            "median_wall_seconds": round(statistics.median(walls), 1),
            "p25_wall_seconds": round(percentile(walls, 0.25), 1),
            "p75_wall_seconds": round(percentile(walls, 0.75), 1),
            "min_wall_seconds": round(min(walls), 1),
            "median_runner_seconds": round(statistics.median(runner), 1),
            "median_stages": {
                stage: round(statistics.median(item["stages"][stage] for item in items), 1)
                for stage in STAGES
            },
            "pipeline": items[0]["pipeline"],
            "cache": items[0]["cache"],
            "shards": items[0]["shards"],
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect CI timings from GitHub Actions")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--cache", type=Path, default=Path(".metrics-cache/runs.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=300, help="runs per workflow to consider")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN must be set", file=sys.stderr)
        return 2

    cached: dict[str, dict[str, Any]] = {}
    if args.cache.exists():
        cached = {str(r["id"]): r for r in json.loads(args.cache.read_text(encoding="utf-8"))}

    fetched = 0
    records: dict[str, dict[str, Any]] = {}
    for workflow, pipeline in WORKFLOWS.items():
        for run in fetch_runs(args.repo, token, workflow, args.limit):
            if run.get("conclusion") != "success" or run.get("run_attempt", 1) != 1:
                continue  # failed runs and re-runs would distort the timings
            key = str(run["id"])
            if key in cached:
                records[key] = cached[key]
                continue
            jobs = api_get(
                f"/repos/{args.repo}/actions/runs/{run['id']}/jobs",
                token,
                {"per_page": 100, "filter": "latest"},
            )["jobs"]
            records[key] = summarise_run(run, pipeline, jobs)
            fetched += 1

    ordered = sorted(records.values(), key=lambda record: record["created_at"])
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(json.dumps(ordered), encoding="utf-8")

    output = {
        "repository": args.repo,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": ordered,
        "summary": aggregate(ordered),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"{len(ordered)} runs ({fetched} fetched from the API) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
