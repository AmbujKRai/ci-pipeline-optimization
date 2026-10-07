# The pipelines, technique by technique

Both CI pipelines do **exactly the same work**:

- ruff lint and format check, plus the bandit security scan;
- all 581 tests with coverage;
- a Docker image build and a container smoke test.

Only *how* that work is scheduled and cached differs, which is what makes the comparison
fair.

| | Baseline (`ci-baseline.yml`) | Optimized (`ci-optimized.yml`) |
|---|---|---|
| Jobs | 1 sequential job | 7 jobs, 6 of them start at once |
| Tests | one process, serial | 4 shards × 4 workers each = 16 processes |
| Python dependencies | `pip install` every run | whole virtualenv restored from cache |
| Docker build | `--no-cache --pull` | BuildKit layer cache in the Actions cache |
| Coverage gate | 85 % in the single job | per-shard data merged, then gated at 85 % |

## 1. Job-level parallelism

GitHub Actions starts every job without `needs:` immediately, each on its own fresh VM.
Static checks, the four test shards and the Docker build don't depend on each other, so
they run side by side. Only the *Test report* job waits (`needs: tests`), because it merges
the shards' results.

Pipeline time becomes `max(branches)` instead of `sum(steps)`.

## 2. Test sharding with pytest-split

```yaml
strategy:
  matrix:
    group: [1, 2, 3, 4]
steps:
  - run: pytest --splits 4 --group ${{ matrix.group }} --splitting-algorithm least_duration ...
```

- `.test_durations`, committed to the repo, records how long every test took (pytest-split's
  `--store-durations`).
- `least_duration` is a greedy *longest-processing-time-first* scheduler: it gives each test,
  slowest first, to the shard with the least work so far. The four shards end up with the
  same estimated time (23.7 s each), not just the same number of tests.
- Sharding by test *count* would be unbalanced here: the report tests take about 1 s each,
  while most unit tests take milliseconds.
- The groups partition the suite exactly. The project checks that every one of the 581 tests
  runs in one and only one shard.

## 3. Parallel workers inside each shard (pytest-xdist)

`pytest -n auto --dist worksteal` starts one worker per vCPU (4 on a GitHub-hosted runner).
With `worksteal`, a worker that runs out of tests takes queued tests from a busy one. That
copes well with uneven test durations.

**Isolation makes this safe.** Every test gets its own app with a private in-memory
database, there is no shared global state, and each shard writes uniquely named
coverage and JUnit files.

## 4. Dependency caching

`.github/actions/python-env` restores the **entire virtualenv**, not just pip's download
cache, so a cache hit skips `pip install` completely:

```yaml
key: venv-${{ runner.os }}-py${{ steps.python.outputs.python-version }}-${{ hashFiles('requirements.txt', 'requirements-dev.txt') }}
```

**Invalidation.** The key changes when, and only when:

- a pinned dependency changes, or
- the runner's Python patch version changes. A virtualenv contains absolute paths to its
  interpreter, so a mismatch would break it.

There are deliberately no `restore-keys`: a near-miss would restore a stale environment.

## 5. Docker layer caching

- **Layer order in the `Dockerfile`:**
  1. `COPY requirements.txt`
  2. `pip install` (slow, rarely changes)
  3. `COPY app` (fast, changes every commit)
  4. build arguments that change every build (`GIT_SHA`, `BUILD_TIME`)

  A code-only change therefore reuses the dependency layer.
- **Multi-stage build.** Pip and its caches stay in the builder stage. The runtime image is
  `python:3.12-slim` plus a virtualenv, running as a non-root user.
- **Cache mount.** `RUN --mount=type=cache,target=/root/.cache/pip` keeps downloaded wheels
  between builds on the same machine without putting them into the image.
- **Remote cache.** `cache-from/cache-to: type=gha,mode=max` stores every layer, including
  intermediate stages, in the GitHub Actions cache, so ephemeral runners still get layer hits.
- `docker/Dockerfile.naive` shows the opposite (copy everything, then install). Experiment E4
  measures the difference.

## 6. Pipeline hygiene

- `concurrency` cancels superseded pull-request runs.
- `fail-fast` stops the other shards as soon as one fails.
- Shallow checkouts and `timeout-minutes` on every job.
- Pushes that only touch docs skip CI. Pull requests always run CI, so the required checks
  can't get stuck.

## 7. Continuous delivery

`cd.yml` runs after a successful optimized CI on `main`:

1. **Staging.** A throw-away Kubernetes cluster (kind) is created in the runner. The exact
   CI image is loaded with `kind load docker-image` and `k8s/` is applied: 2 replicas,
   readiness and liveness probes on `/health`, resource limits, a read-only root filesystem
   and a non-root user. The job waits for `rollout status`, then runs the smoke tests through
   a port-forward.
2. **Production.** The same image tag is deployed to Render through its deploy hook
   (`&imgURL=…`). The job polls `/version` until the new commit is live, then runs the smoke
   tests against the public URL.
3. **Promotion.** `latest` is moved to the deployed `sha-<commit>` tag with
   `docker buildx imagetools create`; no image is rebuilt.

A manual run with an older tag is a rollback.

## 8. Quality gates

| Gate | Where |
|---|---|
| Lint and format | ruff, *Static checks* job |
| Security scan | bandit, *Static checks* job |
| Tests | every shard; any failure fails *Test report* |
| Coverage ≥ 85 % | merged coverage in *Test report* (each shard alone covers less) |
| Image works | container started; `/health`, `/version` and the non-root user checked |
| Deployment works | smoke tests in staging and production |
| Branch protection | *Static checks*, *Test report* and *Docker build* must pass before merging |

## 9. Jenkins equivalent

`Jenkinsfile` applies the same ideas to Jenkins:

- the virtualenv is cached under `JENKINS_HOME`, keyed by a hash of the requirements files;
- a declarative `parallel` block runs static checks, four shards (`jenkins/run-shard.sh`) and
  the Docker build together;
- JUnit and coverage results are published in Jenkins;
- the image is deployed to a local staging container and smoke-tested.

`Jenkinsfile.baseline` is the sequential, cache-free version. The **Jenkins Verify**
workflow boots this Jenkins on a GitHub runner and requires both jobs to pass.
