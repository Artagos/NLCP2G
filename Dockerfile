# One image, four processes. `docker compose` runs the same image with a
# different command per service — they share a database and differ only in what
# they are responsible for.
#
# Note this image is NOT the sandbox. Learner code never runs in here; it runs in
# `cp-tutor-sandbox` (see sandbox/Dockerfile), which the worker starts as a
# sibling container. Keeping them separate is the point: this image has a network
# and an API key, and that one has neither.

# ---- stage 1: build the web UI ------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /w
# lockfile first, so a source-only change doesn't reinstall the world
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: runtime ---------------------------------------------------------
FROM python:3.12-slim

# Just the docker CLI, not a daemon. The worker uses it to ask the HOST daemon
# (over the mounted socket) to start sandbox containers. ~30MB, against ~300MB
# for the docker.io package.
ARG DOCKER_CLI_VERSION=27.3.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY rules/ rules/
COPY sandbox/ sandbox/
COPY --from=web /w/dist frontend/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 0.0.0.0, not loopback: a container that binds 127.0.0.1 publishes a port that
# reaches nothing.
CMD ["python", "-m", "backend.bot", "--host", "0.0.0.0"]
