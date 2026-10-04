# Production image for ShopLite, written to make the most of the Docker layer cache:
#   1. dependencies are installed before the source code is copied, so a code-only
#      change re-uses the (slow) dependency layer;
#   2. a multi-stage build keeps compilers, pip and caches out of the final image;
#   3. values that change on every build (commit SHA, build time) come last.

ARG PYTHON_IMAGE=python:3.12-slim

# ---- build stage: create a virtualenv with the pinned dependencies -------------------------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.txt .
# The cache mount keeps downloaded wheels between local builds without baking them in.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ---- runtime stage: slim image, non-root user, only what the app needs ---------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    APP_ENV=production \
    DATABASE_URL=sqlite:////tmp/shoplite.db
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# Build metadata changes on every commit, so it is declared last: only these tiny
# layers are rebuilt when nothing else changed.
ARG GIT_SHA=local
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} \
    BUILD_TIME=${BUILD_TIME}
LABEL org.opencontainers.image.title="ShopLite" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}"

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]
CMD ["python", "-m", "app"]
