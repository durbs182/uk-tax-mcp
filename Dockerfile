# syntax=docker/dockerfile:1

# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tooling only in the builder stage
RUN pip install --no-cache-dir hatchling

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Build a wheel so the final stage needs no build tools
RUN pip wheel --no-deps --wheel-dir /wheels .

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="uk-tax-mcp" \
      org.opencontainers.image.description="Deterministic HMRC tax rule engine — MCP server" \
      org.opencontainers.image.source="https://github.com/durbs182/uk-tax-mcp"

# Create a non-root user for the process
RUN addgroup --system mcp && adduser --system --ingroup mcp mcp

WORKDIR /app

# Install the wheel and server extras (mcp, click) but not dev/indexing deps
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl "mcp[cli]>=1.0.0" "click>=8.0" \
    && rm -rf /wheels

# Runtime configuration (override via --env-file or -e flags)
ENV SERVICE_NAME="uk-tax-mcp" \
    LOG_LEVEL="INFO" \
    LOG_FORMAT="json"

USER mcp

# The MCP server speaks stdio; the container is expected to be connected to
# an MCP client that wraps stdin/stdout. Run via `docker run -i` or an MCP
# host that manages the subprocess transport.
ENTRYPOINT ["uk-tax-mcp"]

# ── Stage 3: dev ─────────────────────────────────────────────────────────────
# Extends runtime with the HTTP dev server (FastAPI + uvicorn).
# Used by docker-compose for local smoke-testing — not published to the registry.
FROM runtime AS dev

USER root
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29"
USER mcp

# Reset entrypoint so compose can set the full command cleanly.
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "hmrc_tax_mcp.http_dev:app", \
     "--host", "0.0.0.0", "--port", "8000"]
