"""Experiment E4: Docker build caching, naive vs optimized Dockerfile.

For each Dockerfile it measures four builds, starting from an empty build cache:
  cold               nothing cached yet
  no change          rebuild of the same commit
  code change        one line of application code changed
  dependency change  requirements.txt changed (forces the dependency layer to rebuild)
plus the final image size. Base images are pulled beforehand so network time is excluded.

    python scripts/docker_benchmark.py --repeats 3 --out docker-benchmark.json
    python scripts/docker_benchmark.py --summarize docker-benchmark.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = {"naive": "docker/Dockerfile.naive", "optimized": "Dockerfile"}
SCENARIOS = ("cold", "no_change", "code_change", "dependency_change")
SCENARIO_LABELS = {
    "cold": "Cold build",
    "no_change": "No change",
    "code_change": "Code change",
    "dependency_change": "Dependency change",
}
CODE_FILE = ROOT / "app" / "main.py"
DEPS_FILE = ROOT / "requirements.txt"


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], cwd=ROOT, capture_output=True, text=True, check=True)


def timed_build(dockerfile: str, tag: str) -> float:
    started = time.perf_counter()
    docker("build", "--file", dockerfile, "--tag", tag, ".")
    return round(time.perf_counter() - started, 2)


def touch(path: Path, marker: str) -> str:
    original = path.read_text(encoding="utf-8")
    path.write_text(original + f"\n# benchmark change {marker}\n", encoding="utf-8")
    return original


def measure(variant: str, dockerfile: str, repeat: int) -> dict[str, float]:
    tag = f"bench-{variant}:latest"
    subprocess.run(["docker", "image", "rm", "-f", tag], cwd=ROOT, capture_output=True)
    docker("builder", "prune", "--all", "--force")

    result = {"cold": timed_build(dockerfile, tag), "no_change": timed_build(dockerfile, tag)}

    original = touch(CODE_FILE, f"{variant}-{repeat}")
    try:
        result["code_change"] = timed_build(dockerfile, tag)
    finally:
        CODE_FILE.write_text(original, encoding="utf-8")

    original = touch(DEPS_FILE, f"{variant}-{repeat}")
    try:
        result["dependency_change"] = timed_build(dockerfile, tag)
    finally:
        DEPS_FILE.write_text(original, encoding="utf-8")

    result["size_bytes"] = int(docker("image", "inspect", "--format", "{{.Size}}", tag).stdout)
    return result


def run(repeats: int, out: Path) -> None:
    runs: dict[str, list[dict[str, float]]] = {name: [] for name in VARIANTS}
    for repeat in range(1, repeats + 1):
        for variant, dockerfile in VARIANTS.items():
            measured = measure(variant, dockerfile, repeat)
            runs[variant].append(measured)
            print(f"repeat {repeat} {variant:>9}: {measured}", flush=True)

    summary = {}
    for variant, items in runs.items():
        summary[variant] = {
            scenario: statistics.median(item[scenario] for item in items) for scenario in SCENARIOS
        }
        summary[variant]["size_bytes"] = items[-1]["size_bytes"]
    report = {
        "experiment": "E4 Docker build caching",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repeats": repeats,
        "runs": runs,
        "median": summary,
    }
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"saved {out}")


def summarize(path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    median = report["median"]
    print(f"## Docker build caching (median of {report['repeats']} repeats)\n")
    print("| Scenario | Naive Dockerfile | Optimized Dockerfile | Faster by |")
    print("|---|---:|---:|---:|")
    for scenario in SCENARIOS:
        naive, optimized = median["naive"][scenario], median["optimized"][scenario]
        ratio = f"{naive / optimized:.1f}x" if optimized else "-"
        label = SCENARIO_LABELS[scenario]
        print(f"| {label} | {naive:.1f} s | {optimized:.1f} s | {ratio} |")
    naive_mb = median["naive"]["size_bytes"] / 1e6
    optimized_mb = median["optimized"]["size_bytes"] / 1e6
    ratio = naive_mb / optimized_mb
    print(f"| Image size | {naive_mb:.0f} MB | {optimized_mb:.0f} MB | {ratio:.1f}x |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Docker build caching benchmark")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("docker-benchmark.json"))
    parser.add_argument("--summarize", type=Path, help="print a Markdown table for a report")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.repeats, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
