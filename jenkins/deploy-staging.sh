#!/bin/sh
# Replace the local staging container with the given image and wait until it is healthy.
# The container joins Jenkins' Docker network, so the pipeline reaches it by name.
set -eu
image="$1"
network="${CI_NETWORK:-shoplite-ci}"

docker rm -f shoplite-staging >/dev/null 2>&1 || true
docker run -d --name shoplite-staging --network "$network" -p 8085:8000 \
    -e APP_ENV=jenkins-staging "$image"

for attempt in $(seq 1 30); do
    if curl -fsS http://shoplite-staging:8000/health; then
        echo
        echo "Staging is up: http://localhost:8085"
        exit 0
    fi
    sleep 1
done
echo "Staging did not become healthy" >&2
docker logs shoplite-staging >&2 || true
exit 1
