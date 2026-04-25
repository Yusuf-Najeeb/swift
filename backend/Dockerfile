# syntax=docker/dockerfile:1
# Build from the repository root:
#   docker build -f backend/Dockerfile .

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_CACHE=1

# Node: Evaluator can spawn `npx` for optional MCP (e.g. Serper). `uv`/`uvx`
# is installed above for mcp-server-fetch and other stdio tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    nodejs \
    npm \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY backend ./backend

RUN mkdir -p backend/articles

RUN uv sync --no-dev --frozen

EXPOSE 8000

# Runtime env (e.g. OPENROUTER_API_KEY) is supplied by docker compose / k8s.
# Optional live MCP: cold-start may run `uvx`/`npx` — allow time for /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
