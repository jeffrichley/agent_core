# agent_core staging harness — isolated pre-prod gate.
#
# Boots a candidate agent_core version as a throwaway daemon in a container,
# fully walled off from the live Wren + Pepper daemon on the host. Nothing here
# touches the host bus, ports, vault, or Discord bots.
#
# Install modes (build arg INSTALL_MODE):
#   source (default) — git clone jeffrichley/agent_core@REF and `uv sync` the
#                      workspace. Resolves sibling packages (agent-core-credentials
#                      etc.) BY PATH, so it works even before every package is on
#                      PyPI and regardless of inter-package version pins. This is
#                      the right mode for a staging gate: test the candidate CODE.
#   pypi             — pip install agent-core-bus==VERSION from PyPI. Only works
#                      once ALL dependency packages are published (blocked today
#                      by the new-project rate limit). Doubles as publish-validation.
FROM python:3.12-slim

ARG INSTALL_MODE=source
ARG VERSION=0.8.2
ARG REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /usr/local/bin/uv

# --- install the candidate ---
# source mode: clone + `uv sync --package agent-core-bus` (core + its real deps,
# resolved from the workspace by path; excludes heavy voice/torch). The daemon
# then runs via `uv run --no-sync` from /src.
RUN set -eux; \
    if [ "$INSTALL_MODE" = "source" ]; then \
        git clone --depth 1 --branch "${REF}" https://github.com/jeffrichley/agent_core.git /src; \
        cd /src && UV_PYTHON=python3.12 uv sync --no-dev --package agent-core-bus; \
    elif [ "$INSTALL_MODE" = "pypi" ]; then \
        uv pip install --system "agent-core-bus==${VERSION}"; \
        mkdir -p /src; \
    else \
        echo "unknown INSTALL_MODE=${INSTALL_MODE}"; exit 2; \
    fi

# A tiny launcher that runs the daemon under the right interpreter for either
# mode, plus a `status` helper the shakedown calls.
RUN printf '#!/bin/sh\nif [ -d /src/.venv ]; then exec uv run --project /src --no-sync agent-core "$@"; else exec agent-core "$@"; fi\n' \
      > /usr/local/bin/ac && chmod +x /usr/local/bin/ac

RUN mkdir -p /data
COPY config/staging.yaml /config/staging.yaml

RUN ac --help >/dev/null
RUN echo "mode=${INSTALL_MODE} ref=${REF} version=${VERSION}" > /agent-core-version.txt

EXPOSE 8789
# Foreground daemon — the process under test. Runs until SIGTERM.
CMD ["ac", "bus", "run", "--config", "/config/staging.yaml"]
