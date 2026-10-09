# Setup and operations guide

## 1. Run the application locally (no Docker needed)

Requires Python 3.12 (3.11+ works).

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1      Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app
```

Open <http://localhost:8000> for the dashboard and <http://localhost:8000/docs> for the API.
Demo data (catalogue, coupons, order history) is created on first start.

| Environment variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | HTTP port |
| `DATABASE_URL` | `sqlite:///./shoplite.db` | database location |
| `ADMIN_PASSWORD` | random | password for `admin@shoplite.local` |
| `JWT_SECRET` | random per process | token signing key |
| `BCRYPT_ROUNDS` | `12` | password hashing cost |
| `SEED_DEMO_DATA` | `true` | create demo data in an empty database |

## 2. Run the tests

```bash
pytest -n auto                       # all 581 tests, one worker per CPU core
pytest -p no:xdist                   # serially, like the baseline pipeline
pytest --splits 4 --group 2 -n auto  # one shard, exactly as CI runs it
ruff check . && ruff format --check . && bandit -r app -q
```

### Updating test durations

When tests are added or change a lot, re-record the durations so the shards stay balanced.
New tests without a recorded duration are assigned the average, so this is not urgent.

```bash
pytest -p no:xdist --store-durations
```

## 3. Docker

```bash
docker compose up --build            # builds the image and serves it on port 8000
docker run -p 8000:8000 ghcr.io/ambujkrai/ci-pipeline-optimization:latest
```

**No Docker installed?** Open the repository in **GitHub Codespaces** (Code → Codespaces →
Create). The dev container in `.devcontainer/` has Python 3.12 and its own Docker engine.
Every command in this guide works there, in the browser.

## 4. Jenkins (local demo)

```bash
cp jenkins/.env.example jenkins/.env       # set your own admin password
docker compose -f jenkins/docker-compose.yml up -d --build
```

1. Open <http://localhost:8080> and sign in as `admin`.
2. Run the **ci-optimized** and **ci-baseline** jobs. The Pipeline Graph View shows the
   parallel branches, and the staging container is served on <http://localhost:8085>.
3. Everything Jenkins needs is in `jenkins/`: the plugins, the users, both jobs (in
   `casc.yaml`), and the `Jenkinsfile` / `Jenkinsfile.baseline` in the repository root.
4. The **Jenkins Verify** workflow starts this exact setup on a GitHub runner and fails
   unless both jobs pass.

## 5. Production on Render (one-time)

The production service for this repository is live at
<https://ci-pipeline-optimization-latest.onrender.com/>. To set up your own:

1. Create a free account at <https://render.com>.
2. **New → Web Service → Existing image**, enter
   `ghcr.io/ambujkrai/ci-pipeline-optimization:latest`. The image is public, so no
   credentials are needed.
3. Pick the **Free** instance type. Under *Advanced*, set the health check path to
   `/health`. Optionally set `ADMIN_PASSWORD`.
4. After the first deploy, copy the service URL and, from *Settings*, the **Deploy Hook**.
5. Store them in the repository:

   ```bash
   gh secret set RENDER_DEPLOY_HOOK_URL       # paste the deploy hook when prompted
   gh variable set RENDER_SERVICE_URL --body "https://<your-service>.onrender.com"
   ```

From then on every successful CI run on `main` is deployed automatically: staging first,
then production. Free Render services sleep after 15 idle minutes and take about a minute
to wake up.

## 6. Rollback

Actions → **CD** → *Run workflow*, and enter an older image tag such as `sha-1a2b3c4`. The
tags are listed on the repository's *Packages* page. The rollback still goes through
staging first.

## 7. Repository settings used

| Setting | Value |
|---|---|
| Branch protection on `main` | PR required; checks *Static checks*, *Test report*, *Docker build* must pass |
| GitHub Pages | source: GitHub Actions |
| Environments | `staging`, `production` (add required reviewers for a manual gate), `github-pages` |
| Secrets / variables | `RENDER_DEPLOY_HOOK_URL` (secret), `RENDER_SERVICE_URL` (variable) |
