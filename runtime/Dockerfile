# Agent runtime image: the warm-instance service that runs agent invocations (`python -m collab_runtime`).
# Build from the repository root:
#   docker build -f runtime/Dockerfile -t <image> .
# No credentials are baked in. The runtime fetches its secrets from Secret Manager at run time.

FROM node:22-bookworm-slim

# Claude Code CLI, pinned. Bump deliberately after checking the release notes.
ARG CLAUDE_CODE_VERSION=2.1.322
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY runtime/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY runtime/collab_runtime /app/collab_runtime

# Non-root, no login shell, and a home directory the harness can write its own state into.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin runner
USER runner
WORKDIR /home/runner
ENV PYTHONPATH=/app CLAUDE_BINARY=claude

ENTRYPOINT ["python3", "-m", "collab_runtime"]
