FROM python:3.12-slim-bookworm

# Must be *after* FROM to be available in RUN
ARG WITH_NODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Second layer only if WITH_NODE=1 (Evaluator may spawn `npx` e.g. Serper).
RUN if [ "$WITH_NODE" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
      && rm -rf /var/lib/apt/lists/*; \
    else \
      echo "WITH_NODE=0: skipping nodejs/npm; use --build-arg WITH_NODE=1 for npx (Serper MCP)"; \
    fi

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
