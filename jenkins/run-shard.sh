#!/bin/sh
# Run one duration-balanced shard of the test suite: run-shard.sh GROUP SHARDS
# Each shard writes its own coverage and JUnit files, so shards can run side by side.
set -eu
group="$1"
shards="$2"
COVERAGE_FILE=".coverage.shard-$group" .venv/bin/pytest \
    -n 2 -p no:cacheprovider \
    --splits "$shards" --group "$group" --splitting-algorithm least_duration \
    --cov=app --cov-report= --cov-fail-under=0 \
    --junitxml="reports/junit-shard-$group.xml"
