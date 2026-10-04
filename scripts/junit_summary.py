"""Merge JUnit XML reports from all test shards into one Markdown summary.

    python scripts/junit_summary.py reports/junit-*.xml >> "$GITHUB_STEP_SUMMARY"

Exits with status 1 if any test failed or errored, so it can gate a pipeline.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Shard:
    name: str
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0


def parse(path: Path) -> tuple[Shard, list[tuple[float, str]], list[str]]:
    # The files are produced by our own pytest run inside the job, not by an outside party.
    root = ET.parse(path).getroot()  # nosec B314
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    shard = Shard(name=path.stem.removeprefix("junit-"))
    timings: list[tuple[float, str]] = []
    problems: list[str] = []
    for suite in suites:
        for case in suite.iter("testcase"):
            seconds = float(case.get("time", 0))
            node = f"{case.get('classname', '')}::{case.get('name', '')}"
            shard.tests += 1
            shard.seconds += seconds
            timings.append((seconds, node))
            if case.find("failure") is not None:
                shard.failures += 1
                problems.append(f"FAILED {node}")
            elif case.find("error") is not None:
                shard.errors += 1
                problems.append(f"ERROR {node}")
            elif case.find("skipped") is not None:
                shard.skipped += 1
    return shard, timings, problems


def main(paths: list[str]) -> int:
    if not paths:
        print("usage: junit_summary.py REPORT.xml [REPORT.xml ...]", file=sys.stderr)
        return 2
    shards: list[Shard] = []
    timings: list[tuple[float, str]] = []
    problems: list[str] = []
    for path in sorted(paths):
        shard, shard_timings, shard_problems = parse(Path(path))
        shards.append(shard)
        timings.extend(shard_timings)
        problems.extend(shard_problems)

    total = Shard(name="**Total**")
    for shard in shards:
        total.tests += shard.tests
        total.failures += shard.failures
        total.errors += shard.errors
        total.skipped += shard.skipped
        total.seconds += shard.seconds

    ok = total.failures == 0 and total.errors == 0
    icon = "✅" if ok else "❌"
    print(f"## {icon} Test results: {total.tests} tests across {len(shards)} shard(s)\n")
    print("| Shard | Tests | Failed | Errors | Skipped | Test time (s) |")
    print("|---|---:|---:|---:|---:|---:|")
    for shard in [*shards, total]:
        print(
            f"| {shard.name} | {shard.tests} | {shard.failures} | {shard.errors} "
            f"| {shard.skipped} | {shard.seconds:.1f} |"
        )
    if len(shards) > 1:
        busiest = max(shard.seconds for shard in shards)
        quietest = min(shard.seconds for shard in shards)
        print(f"\nShard balance: slowest {busiest:.1f} s, fastest {quietest:.1f} s.")

    print("\n<details><summary>Slowest 10 tests</summary>\n")
    print("| Seconds | Test |\n|---:|---|")
    for seconds, node in sorted(timings, reverse=True)[:10]:
        print(f"| {seconds:.2f} | `{node}` |")
    print("\n</details>")

    if problems:
        print("\n### Failures\n")
        for line in problems:
            print(f"- `{line}`")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
