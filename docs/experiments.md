# Experiments and results

All CI measurements were taken on standard GitHub-hosted Linux runners: 4 vCPU and 16 GB RAM
for public repositories. The live, always-current version of these numbers is on the
[pipeline dashboard](https://ambujkrai.github.io/ci-pipeline-optimization/). The snapshot used
here is [`results/ci-metrics.json`](results/ci-metrics.json), taken on 7 October 2026 from 53
successful runs.

## How the measurements were taken

- **Same work in every configuration.** Each pipeline runs:
  - ruff and bandit;
  - all 581 tests with coverage;
  - a Docker build and a container smoke test.

  Only scheduling and caching change.
- **Same commit.** Experiment runs were started by hand on `main` with
  `scripts/run_experiments.py`, which interleaves the configurations round-robin. A slow period
  on GitHub therefore affects every configuration equally.
- **Wall-clock time:** from the first job starting to the last job finishing. Queueing
  *before* the pipeline starts is excluded.
- **Runner time:** the sum of all job durations, i.e. what a paid CI plan bills.
- **Medians and IQR.** Shared runners are noisy, so we report medians and the interquartile
  range (middle 50%). A warm-up run that only fills the caches is labelled and excluded.
- **Simulated latency, stated openly.** The 581 tests include about 50 external-service tests
  whose stub clients simulate network latency (200–400 ms per call, adjustable with
  `TEST_LATENCY_SCALE`). All other test time is real computation: bcrypt, SQL aggregates over
  thousands of orders, and API calls.

## E1: orchestration × caching (2 × 2)

| Configuration | Runs | Median wall time | Middle 50% | Median runner time |
|---|---:|---:|---:|---:|
| C1 Sequential, no cache (**baseline**) | 11 | **2 m 47 s** (167 s) | 144–181 s | 167 s |
| C2 Sequential + caching | 5 | 2 m 41 s (161 s) | 155–167 s | 161 s |
| C3 Parallel, no cache | 5 | 1 m 02 s (62 s) | 60–67 s | 203 s |
| C4 Parallel + caching (**optimized**) | 18 | **46 s** (45.5 s) | 41–49 s | 153 s |

| Effect | Speed-up (wall-clock) |
|---|---|
| Both techniques (C1 → C4) | **3.7×** faster: 2 minutes saved per run (−73%) |
| Parallelism alone (C1 → C3) | 2.7× |
| Caching alone, sequential (C1 → C2) | 1.04× |
| Caching on top of parallelism (C3 → C4) | 1.36× (−27% wall-clock, −25% runner time) |

Median time per stage, on the critical path:

| Configuration | Python setup | Tests | Docker build |
|---|---:|---:|---:|
| C1 | 11 s | 118 s | 23 s |
| C2 | 2 s | 135 s | 19 s |
| C3 | 13 s | 21 s | 26 s |
| C4 | 3 s | 21 s | 21.5 s |

**What this shows**

1. **Parallelism gives most of the gain.** Serial tests take about 2 minutes on a runner.
   With 4 shards × 4 xdist workers, the slowest shard takes about 21 s.
2. **Caching barely changes the sequential pipeline.** Setup (about 11 s) and the Docker build
   (about 23 s) are small next to 2 minutes of tests. The cache saves about 10–15 s, which is
   within the noise of the test stage.
3. **Caching matters once work is parallel.** Every one of the 7 parallel jobs pays the setup
   cost, so caching removes it 7 times. C3 → C4 cuts wall-clock time by 27% and runner time
   by 25%.
4. **The optimized pipeline is faster *and* cheaper than the baseline.** Parallel jobs add
   per-job overhead (C3 uses 22% more compute than C1). Caching wins that back: C4 uses 8% less
   runner time than C1 while being 3.7× faster.

## E2: how many shards?

Optimized pipeline with caching on, varying the number of test shards:

| Shards | Runs | Median wall time | Median runner time |
|---:|---:|---:|---:|
| 1 | 3 | 74 s | 96 s |
| 2 | 3 | 51 s | 115 s |
| **4** (default) | 18 | **45.5 s** | 153 s |
| 6 | 3 | 39 s | 182 s |
| 8 | 3 | 40 s | 201 s |

- **Diminishing returns (Amdahl's law).** Going from 1 to 2 shards saves 23 s; going from 4 to
  8 saves about 5 s.
- **Why the gains stop.** Each shard job has a fixed cost (runner start, checkout, cache
  restore). Once the shards are short, the *Docker build* job (about 20 s) and the
  *Test report* job that follows the shards decide the wall-clock time, not the tests.
- **Cost grows steadily.** Runner time doubles from 1 to 8 shards.
- **Choice: 4 shards.** About 6 s slower than the fastest setting (6 shards), but it uses 16%
  less compute than 6 shards and 24% less than 8.

## E3: parallel workers on one machine (laptop)

`scripts/benchmark_local.py` on an 8-logical-CPU Windows laptop (4 physical cores), median of 3
runs each ([`results/local_parallelism.json`](results/local_parallelism.json)):

| pytest-xdist workers | Median wall time | Speed-up | Efficiency |
|---|---:|---:|---:|
| serial | 99.2 s | 1.00× | 1.00 |
| 2 | 57.4 s | 1.73× | 0.86 |
| 4 | 32.9 s | 3.01× | 0.75 |
| 8 | 28.4 s | 3.49× | 0.44 |

Efficiency (speed-up ÷ workers) falls as workers are added:

- process start-up, test collection and the serial parts of the run are not parallel;
- beyond 4 workers, logical CPUs share physical cores.

## E4: Docker build caching

`docker-cache-benchmark.yml` on a GitHub runner: naive vs optimized Dockerfile, starting each
repeat from an empty build cache, median of 3 repeats
([`results/docker-benchmark.json`](results/docker-benchmark.json)):

| Scenario | Naive `docker/Dockerfile.naive` | Optimized `Dockerfile` | Difference |
|---|---:|---:|---:|
| Cold build (empty cache) | 6.2 s | 7.9 s | optimized 1.3× slower |
| Rebuild, nothing changed | 0.1 s | 0.1 s | same |
| Rebuild after a code change | 6.0 s | **0.8 s** | **7.6× faster** |
| Rebuild after a dependency change | 6.1 s | 5.0 s | 1.2× faster |
| Image size | 1,173 MB | **184 MB** | **6.4× smaller** |

**Why each scenario behaves as it does**

- **Code change.** The naive Dockerfile copies the whole project *before* `pip install`, so
  any code change invalidates the dependency layer and every package is reinstalled. The
  optimized Dockerfile installs dependencies from `requirements.txt` first and copies the code
  afterwards, so only the small `COPY app` layer is rebuilt.
- **Dependency change.** Both reinstall. The optimized build is still faster because the
  BuildKit pip cache mount keeps already downloaded wheels.
- **Cold build.** The optimized build is slightly slower because the multi-stage build creates
  and copies a virtualenv. This one-off cost is repaid on the first code change.
- **Image size.** The slim base image and the multi-stage build (no pip, no build caches)
  make the image 6.4× smaller, which also speeds up every pull and deployment.

## Jenkins

The **Jenkins Verify** workflow ran both Jenkins pipelines on one 4-vCPU runner:

| Pipeline | Duration |
|---|---|
| `ci-baseline` | 127 s |
| `ci-optimized` | 57 s |

The optimized run was its first, so its virtualenv cache was still empty, and it was still
**2.2× faster**.

## Threats to validity

- **Runner noise.** Shared runners vary from run to run (the baseline's middle 50% spans
  144–181 s). We used several runs per configuration and report medians.
- **Runner queueing.** More parallel jobs mean more chances to wait for a free runner. One
  shard once waited 38 s. Queueing before the first job is excluded from wall-clock time;
  queueing of later jobs is included, as a developer would experience it.
- **Simulated latency** in the external-service tests (documented above).
- **Scale.** This test suite is small. Larger suites (10–30 minutes) gain much more in absolute
  terms, but the shape of the results should hold: big gains from parallelism, diminishing
  returns from more shards, and caching that pays off most in parallel pipelines.

## Reproducing the experiments

```bash
python scripts/benchmark_local.py --repeats 3        # E3, on your machine
python scripts/run_experiments.py e1 --repeats 5     # E1, needs the GitHub CLI
python scripts/run_experiments.py e2 --repeats 3     # E2
python scripts/run_experiments.py e4                 # E4
```

The Pipeline Metrics workflow picks the new runs up automatically and refreshes the dashboard.
