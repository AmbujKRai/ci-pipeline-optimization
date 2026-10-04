"""Trigger Jenkins jobs through the REST API and wait for them to finish.

    JENKINS_URL=http://localhost:8080 JENKINS_USER=admin JENKINS_PASSWORD=... \
        python3 jenkins/verify.py ci-optimized ci-baseline

Exits non-zero unless every job ends in SUCCESS. Used by .github/workflows/jenkins-verify.yml
to prove the Jenkins setup works without anyone having to run Docker locally.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("JENKINS_URL", "http://localhost:8080").rstrip("/")
USER = os.environ.get("JENKINS_USER", "admin")
PASSWORD = os.environ["JENKINS_PASSWORD"]
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()

# Jenkins ties the CSRF crumb to the session cookie, so keep cookies between requests.
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(path: str, method: str = "GET", headers: dict[str, str] | None = None):
    url = path if path.startswith("http") else f"{BASE}{path}"
    request = urllib.request.Request(
        url, method=method, headers={"Authorization": AUTH, **(headers or {})}
    )
    return opener.open(request, timeout=30)


def get_json(path: str) -> dict:
    with call(path.rstrip("/") + "/api/json") as response:
        return json.load(response)


def wait_until_ready(jobs: list[str], timeout: int = 420) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for job in jobs:
                get_json(f"/job/{job}")
            print("Jenkins is up and the jobs exist", flush=True)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(5)
    raise SystemExit("Jenkins did not become ready in time")


def run_job(job: str, crumb: dict[str, str]) -> str:
    with call(f"/job/{job}/build", method="POST", headers=crumb) as response:
        queue_url = response.headers["Location"]
    print(f"{job}: queued", flush=True)

    build_url = None
    for _ in range(120):
        item = get_json(queue_url)
        if item.get("executable"):
            build_url = item["executable"]["url"]
            break
        time.sleep(2)
    if build_url is None:
        raise SystemExit(f"{job} never left the queue")

    started = time.time()
    while True:
        build = get_json(build_url)
        if not build["building"]:
            break
        time.sleep(5)
    result = build["result"]
    print(f"{job}: {result} in {time.time() - started:.0f} s ({build_url})", flush=True)
    if result != "SUCCESS":
        with call(build_url + "consoleText") as response:
            print(response.read().decode(errors="replace")[-6000:])
    return result


def main(jobs: list[str]) -> int:
    wait_until_ready(jobs)
    issuer = get_json("/crumbIssuer")
    crumb = {issuer["crumbRequestField"]: issuer["crumb"]}
    results = {job: run_job(job, crumb) for job in jobs}
    print(json.dumps(results, indent=2))
    return 0 if all(result == "SUCCESS" for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["ci-optimized", "ci-baseline"]))
