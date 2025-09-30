# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS app

# --- System deps (minimal) ----------------------------------------------------
# If your requirements need native builds, uncomment build-essential (gcc, etc).
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates curl \
      build-essential \
      # git \
    && rm -rf /var/lib/apt/lists/*

# --- Install uv ---------------------------------------------------------------
# Installs to /root/.local/bin/uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# --- Runtime: create a venv managed by uv ------------------------------------
# Using a venv avoids --break-system-packages and keeps things tidy.
ENV VIRTUAL_ENV=/opt/venv
RUN uv venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# --- Prepare workdir & copy only requirements first for better caching --------
WORKDIR /app
COPY requirements.txt /app/requirements.txt

# Install deps with uv; cache compiled wheels between builds.
#   --system is NOT used since we install into the venv.
#   UV_LINK_MODE=copy ensures layer reproducibility (no hardlinks across layers).
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r /app/requirements.txt

# --- Copy application code ----------------------------------------------------
COPY mesh.py /app/
COPY main /app/
# If "main" is a shell script, uncomment:
# RUN chmod +x /app/main

# Optional: include example config for local testing
COPY config.json.example /app/config.json

# --- (Optional) security hardening -------------------------------------------
# Create a non-root user; ensure app/ is readable.
# RUN useradd -m -u 10001 -s /usr/sbin/nologin appuser && chown -R appuser:appuser /app /opt/venv
# USER appuser

# --- Entrypoint/CMD -----------------------------------------------------------
# Choose one:
# CMD ["./main"]
# or
# CMD ["python", "-m", "mesh"]
