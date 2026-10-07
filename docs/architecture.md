# Architecture

## Delivery pipeline at a glance

```mermaid
flowchart LR
    dev([Developer]) -- "git push / pull request" --> gh[(GitHub repository)]

    subgraph actions [GitHub Actions]
        ci["CI (Optimized)<br/>parallel jobs · test shards · caches"]
        base["CI (Baseline)<br/>sequential · no caches"]
        cd["CD<br/>staging → production"]
        metrics["Pipeline Metrics<br/>timings → dashboard"]
    end

    gh --> ci
    gh --> base
    ci -- "image sha-&lt;commit&gt;" --> ghcr[(GitHub Container Registry)]
    ci -- "on success (main)" --> cd
    ghcr --> cd
    cd --> kind["Staging<br/>Kubernetes (kind) in the CD runner"]
    cd --> render["Production<br/>Render web service"]
    ci --> metrics
    base --> metrics
    metrics --> pages["GitHub Pages<br/>results dashboard"]

    gh -. "same Jenkinsfiles" .-> jenkins["Jenkins (Docker, local demo)<br/>baseline + optimized jobs"]
```

| Piece | Tool | Where it runs |
|---|---|---|
| Version control | Git + GitHub (protected `main`, pull requests, required checks) | github.com |
| Build and test | Python 3.12, pytest, pytest-xdist, pytest-split, coverage, ruff, bandit | GitHub-hosted runners |
| Container | Docker (BuildKit), multi-stage `Dockerfile` | GitHub-hosted runners, Codespaces, any Docker host |
| Registry | GitHub Container Registry (`ghcr.io`) | github.com |
| Staging | Kubernetes in Docker (`kind`), manifests in `k8s/` | inside the CD runner (ephemeral) |
| Production | Render free web service running the exact image | render.com |
| Metrics | GitHub REST API → static dashboard | GitHub Pages |
| Second CI server | Jenkins LTS, Configuration as Code | local Docker / Codespaces (verified in CI) |

## Optimized CI pipeline

```mermaid
flowchart LR
    T([push or pull request]) --> S[Static checks<br/>ruff + bandit]
    T --> A[Tests shard 1/4]
    T --> B[Tests shard 2/4]
    T --> C[Tests shard 3/4]
    T --> D[Tests shard 4/4]
    T --> K[Docker build<br/>+ container smoke test]
    A & B & C & D --> R[Test report<br/>merged coverage ≥ 85 %]
    K -- "main only" --> P[(push sha tag to GHCR)]
```

Every box starts at the same time on its own runner. The pipeline takes as long as its
slowest branch rather than the sum of all of them.

## Continuous delivery

```mermaid
sequenceDiagram
    participant CI as CI (Optimized)
    participant CD as CD workflow
    participant R as GHCR
    participant K as Staging (kind)
    participant P as Production (Render)
    CI->>R: push image sha-<commit>
    CI-->>CD: workflow_run (success on main)
    CD->>R: pull sha-<commit>
    CD->>K: kind load + kubectl apply -k k8s/
    K-->>CD: rollout complete
    CD->>K: smoke tests (port-forward)
    CD->>P: deploy hook with imgURL = sha-<commit>
    P-->>CD: /version reports <commit>
    CD->>P: smoke tests
    CD->>R: tag sha-<commit> as latest
```

- **Build once, deploy everywhere.** The image that passed CI is the one that runs in
  staging and production; nothing is rebuilt.
- **Immutable tags.** Images are tagged `sha-<commit>`. `latest` only moves after a
  successful deployment.
- **Rollback.** Run the CD workflow by hand with an older tag.

## Application

```mermaid
flowchart TB
    client([Browser / API client]) --> api
    subgraph app [ShopLite: FastAPI]
        api[Routers<br/>auth · products · orders · coupons · reports · ui]
        svc[Services<br/>pricing · GST tax · inventory · orders · payments · notifications · reports]
        orm[SQLAlchemy models]
        api --> svc --> orm
    end
    orm --> db[(SQLite)]
    svc --> pay[Payment gateway stub]
    svc --> mail[E-mail stub]
```

- **Routers** handle HTTP only. All business rules live in `app/services/`, which is why
  most of the 581 tests are fast unit tests.
- **Money** is stored as integer paise.
- **GST:** CGST + SGST inside Maharashtra, IGST for other states.
- **Payment gateway:** the stub is idempotent per key and supports retries with backoff.
- **Probes:** `/health` serves liveness and readiness checks. `/version` proves which commit
  is deployed.
