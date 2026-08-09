# syntax=docker/dockerfile:1

# Defaults to amd64 regardless of the host building it: Tectonic (see the
# `tectonic` stage below) only publishes an aarch64 Linux build against
# musl, not glibc, so its installer script 404s on an arm64 host running a
# glibc base image (Debian/python:slim) — confirmed by testing this build on
# an Apple Silicon Mac. The real deployment target (EC2) is x86_64, hence
# the default; override with `--build-arg TARGET_PLATFORM=linux/arm64` only
# if that ever changes (e.g. a Graviton EC2 instance) and Tectonic's musl
# build has been substituted in accordingly. Docker Desktop's bundled QEMU
# emulation builds the amd64 default fine on arm64 dev machines, just
# slower than a native build.
ARG TARGET_PLATFORM=linux/amd64

FROM --platform=$TARGET_PLATFORM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

# Tectonic (resume tailoring's LaTeX-to-PDF compiler — see
# app/resume/compile_pdf.py) is a single self-contained engine, not a full
# TeX Live install, fetched via its own official installer script rather
# than an apt package (Debian doesn't ship one). Isolated to its own stage
# so the curl/ca-certificates layer used only to fetch it never lands in
# the runtime image.
FROM --platform=$TARGET_PLATFORM debian:bookworm-slim AS tectonic
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tectonic-install
RUN curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh

FROM --platform=$TARGET_PLATFORM python:3.12-slim AS runtime

# fontconfig/harfbuzz/graphite2 are Tectonic's own runtime dependencies for
# font discovery/shaping (see https://tectonic-typesetting.github.io) —
# installed here regardless of whether a given Tectonic release binary is
# statically linked, since the cost is small and a missing shared library
# would otherwise only surface the first time someone tailors a resume.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates fontconfig libharfbuzz0b libgraphite2-3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tectonic /tectonic-install/tectonic /usr/local/bin/tectonic

RUN useradd --system --create-home --uid 10001 jobs
WORKDIR /app

COPY --from=builder --chown=jobs:jobs /app/.venv /app/.venv
COPY --from=builder --chown=jobs:jobs /app/app /app/app
COPY --chown=jobs:jobs scripts ./scripts
COPY --from=builder --chown=jobs:jobs /app/pyproject.toml /app/uv.lock ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/.cache

USER jobs
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
