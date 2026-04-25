# Swift — Technical Writer AI Platform

Swift is a multi-agent article generation platform. An **Orchestrator**
coordinates a **Writer ↔ Evaluator** loop until the draft scores highly
enough, then hands the approved article to an **Image Agent** which
fills in illustrations via [Pollinations.ai](https://pollinations.ai).

- **Backend**: FastAPI + OpenAI Agents SDK (routed through OpenRouter)
- **Streaming**: Server-Sent Events
- **MCP**: FastMCP server exposing Swift's tools to MCP clients
- **Frontend**: Next.js 15 (App Router) + Tailwind; live SSE via `app/api/stream` BFF
- **Storage**: local `backend/articles/` folder *(Azure Blob backup later)*

## Build status

Following a 12-step plan — current state:

- [x] **Step 1** — FastAPI + OpenRouter connection + config
- [x] **Step 2** — Writer Agent (OpenAI Agents SDK)
- [x] **Step 2b** — Writer agent as MCP consumer (default: `mcp-server-fetch`)
- [x] **Step 3** — Evaluator Agent + structured JSON output
      (gpt-4o by default, fact-checks via `mcp-server-fetch` +
      Serper Google search when `SERPER_API_KEY` is set)
- [x] **Step 4** — Orchestrator wiring Writer ↔ Evaluator loop
      (deterministic Python; retries until `score >= 7` or
      `SWIFT_ORCHESTRATOR_MAX_RETRIES` exhausted)
- [x] **Step 5** — Image Agent substituting `[IMAGE: ...]`
      markers for Pollinations.ai URLs (no auth needed;
      generate-on-GET, so zero latency on our side). Writer
      also emits `` ```mermaid `` fenced blocks for diagrams;
      Image Agent surfaces them as `FinalArticle.diagrams`
      without modifying the body — the frontend renders them.
- [x] **Step 6** — FastMCP server layer. `write_article` is
      exposed as an MCP tool; the Streamable-HTTP transport is
      mounted into FastAPI at `SWIFT_MCP_SERVER_MOUNT_PATH`
      (default `/mcp`) and optionally bearer-token authenticated.
      Stdio entrypoint for local MCP clients:
      `uv run python -m backend.mcp.server`.
- [x] **Step 7** — FastAPI SSE streaming endpoint
      (`POST /api/generate/stream` returns a Server-Sent Events
      stream of typed `PipelineEvent` objects — one per pipeline
      transition — ending with the full `FinalArticle` payload.
      Keep-alive pings survive long Writer calls behind proxies.)
- [x] **Step 8** — Next.js UI: compact form (topic, audience, tone) +
      `ArticlePreview` + one-line status under the form; pipeline options
      (length, images, retries) use backend defaults
      (Markdown + Mermaid), BFF at `POST /api/stream` → FastAPI
      `POST /api/generate/stream`. Copy `frontend/.env.local.example`
      to `frontend/.env.local` and set `NEXT_PUBLIC_API_URL` if the
      API is not on `http://127.0.0.1:8000`.
- [x] **Step 9** — `file_manager.py` for article storage (saved `.md` under
      `backend/articles/`, list + download API, UI list)
- [x] **Step 10** — Docker: `backend/Dockerfile`, `frontend/Dockerfile`,
      `docker-compose.yml` (named volume for `backend/articles/`)
- [ ] Step 11 — Azure Container Apps + ACR
- [ ] Step 12 — GitHub Actions CI/CD

## Prerequisites

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) — Swift uses uv as its package manager
  (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An **OpenRouter API key** — https://openrouter.ai/keys
- **Node.js 20+** and npm — for the Next.js app in `frontend/`

## Quickstart

```bash
# 1. Configure env
cp .env.example .env
# then open .env and paste your OPENROUTER_API_KEY

# 2. Install deps (creates .venv and uv.lock-pinned packages)
uv sync

# 3. Start the API (from the project root)
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 4. In another terminal: Next.js (from the project root)
cd frontend
cp .env.local.example .env.local   # optional; default API URL is http://127.0.0.1:8000
npm install                         # first time only
npm run dev
# → http://localhost:3000
```

`uv sync` creates `.venv/` at the project root and installs every
runtime dependency in `pyproject.toml` at the versions pinned in
`uv.lock`. Add a new package with `uv add some-pkg`. The backend
Dockerfile uses `uv sync --no-dev` (equivalent to `uv sync` until a dev
group is added back to `pyproject.toml`).

Then:

```bash
curl http://localhost:8000/health
# → {"status":"ok","service":"Swift Writer"}

curl http://localhost:8000/config
# → shows the currently wired OpenRouter model slugs
```

Interactive API docs: <http://localhost:8000/docs>

### Docker (Step 10)

Requires [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2.

```bash
cp .env.example .env
# edit .env — at minimum set OPENROUTER_API_KEY

docker compose up --build
```

- **UI:** <http://localhost:3000>
- **API:** <http://localhost:8000> (e.g. `/docs`, `/health`), MCP (if enabled) at `/mcp`
- **Saved articles** are stored in a Compose named volume (`articles_data` → `/app/backend/articles` in the backend container)

The frontend image is built with `NEXT_PUBLIC_API_URL=http://backend:8000` so the Next.js BFF can reach the API on the Compose network. The first cold start may still wait on `uvx` / `npx` MCP helpers; the backend health check allows up to two minutes before the frontend starts.

**Production hardening (recommended when the network is not just your laptop):**

- Set **`SWIFT_API_BEARER_TOKEN`** to a long random string in the **root** `.env`. The same value must be in **`frontend/.env.local`** as `SWIFT_API_BEARER_TOKEN` so the Next BFF can call the API. When this is set, the pipeline, article file APIs, and `GET /config` require `Authorization: Bearer <token>`. **`GET /health` and `GET /` stay open** for probes.
- Set **`SWIFT_MCP_SERVER_BEARER_TOKEN`** if you expose the mounted MCP HTTP surface; the server logs a warning on startup if MCP is enabled without it.
- Put **rate limits** and **IP allow lists** at your reverse proxy or API gateway; this app does not replace those.
- For a **new public origin** (e.g. `https://app.example.com`), add it to **`SWIFT_CORS_ORIGINS`**.

**Why the API “hangs” after a request (first time):** the Writer and
Evaluator start separate [`uvx mcp-server-fetch`](https://github.com/modelcontextprotocol/servers) subprocesses. On a **cold**
machine or cache, `uv` downloads that package once per process; you may
see *Resolving dependencies…* in the same terminal as Uvicorn for a
minute or two, and the SSE stream will not finish until those servers
are up. **Mitigations:** (1) pre-warm: `UV_NO_PROGRESS=1 uvx
mcp-server-fetch </dev/null` once; (2) for UI-only work, set
`SWIFT_WRITER_MCP_FETCH_ENABLED=0` and
`SWIFT_EVALUATOR_MCP_FETCH_ENABLED=0` in `.env` so the pipeline uses
no MCP tools and starts immediately; (3) with `SERPER_API_KEY`, the
Evaluator also spawns `npx` the first time — same idea.

## Using Swift as an MCP server

Swift exposes its article-generation pipeline as a standard
[MCP](https://modelcontextprotocol.io) tool, so any MCP client
(Claude Desktop, Cursor, other agents) can invoke `write_article`
directly. Two transports are supported; they share the same tool
registry.

### HTTP (Streamable-HTTP) — cloud & networked clients

Mounted into the FastAPI app at `SWIFT_MCP_SERVER_MOUNT_PATH`
(default `/mcp`). Once `uv run uvicorn backend.main:app` is up, the
endpoint is live at <http://localhost:8000/mcp/>. Set
`SWIFT_MCP_SERVER_BEARER_TOKEN` for public deployments — every
request under the mount path then needs
`Authorization: Bearer <token>`; the REST endpoints (`/`, `/health`,
`/config`) stay open. Disable the surface entirely with
`SWIFT_MCP_SERVER_ENABLED=false`.

### Stdio — local clients (Claude Desktop, `mcp` CLI)

```bash
uv run python -m backend.mcp.server
```

The process speaks MCP JSON-RPC over stdin/stdout; the bearer-token
setting is ignored (the OS process boundary is the trust boundary).
For Claude Desktop, add an entry to its config file:

```json
{
  "mcpServers": {
    "swift-writer": {
      "command": "uv",
      "args": ["run", "python", "-m", "backend.mcp.server"],
      "cwd": "/absolute/path/to/swift-writer"
    }
  }
}
```

### Calling the tool

One tool is advertised: `write_article(topic, tone?, length?,
keywords?, audience?, extra_notes?, max_retries?)`. Only `topic` is
required; everything else falls back to the same defaults as
`ArticleBrief`. The return value is the full `FinalArticle`
(Markdown body with images substituted + structured diagram index).

## Streaming the pipeline to a browser (Step 7)

Human callers don't want a 60-second blank tab while the Orchestrator
drafts, grades, and revises. `POST /api/generate/stream` opens a
Server-Sent Events connection and emits a typed event for every
pipeline transition:

| Event                  | When                                  | Useful for                      |
| ---------------------- | ------------------------------------- | ------------------------------- |
| `run.started`          | Brief accepted                        | Show "working on it…"           |
| `attempt.started`      | Revision iteration begins             | "Attempt N of M" label          |
| `writer.completed`     | Draft ready for this iteration        | Reveal title, word count        |
| `evaluator.completed`  | Feedback ready                        | Show score, strengths/weaknesses |
| `images.started`       | Image Agent begins substitution       | Progress indicator              |
| `images.completed`     | Images + diagrams counted             | Confirm illustrations landed    |
| `run.completed`        | Terminal — full `FinalArticle`        | Render the article              |
| `run.failed`           | Terminal — pipeline raised            | Show error, close the spinner   |

### Curl

```bash
curl -N -X POST http://localhost:8000/api/generate/stream \
  -H 'Content-Type: application/json' \
  -d '{"brief":{"topic":"Redis vs Memcached","length":"short"},"max_retries":1}'
```

The `-N` (no-buffer) flag is important — curl otherwise waits for
the whole body. Each frame is `event: <type>\ndata: <json>\n\n`.

### Browser (fetch + ReadableStream)

```ts
const response = await fetch("/api/generate/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    brief: { topic: "Redis vs Memcached", length: "short" },
    max_retries: 1,
  }),
});

const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
// then parse SSE frames — the Next.js client in Step 8 ships a helper
```

Native `EventSource` is not used because SSE via `EventSource` only
supports GET and the brief is carried in a JSON body.

## Project layout

```
swift-writer/
├── backend/
│   ├── agents/           # Agent definitions + OpenRouter provider wiring
│   │   ├── providers.py    # configure_openrouter(), openrouter_model()
│   │   ├── schemas.py       # ArticleBrief, EvaluatorFeedback, WriterOutput, ArticleRun, FinalArticle
│   │   ├── mcp_clients.py   # MCP server factory — Step 2b
│   │   ├── writer.py        # build_writer_agent() — Step 2 + 2b
│   │   ├── evaluator.py     # build_evaluator_agent() — Step 3
│   │   ├── orchestrator.py  # orchestrate_article() — Step 4
│   │   ├── image_agent.py   # illustrate_article() — Step 5
│   │   └── events.py        # PipelineEvent union — Step 7
│   ├── api/              # FastAPI routers — SSE streaming endpoint (Step 7)
│   ├── mcp/              # FastMCP server (Step 6)
│   ├── storage/          # File + blob storage (Step 9)
│   ├── articles/         # Generated .md output (gitignored)
│   ├── config.py         # pydantic-settings Settings + get_settings()
│   └── main.py           # FastAPI app factory + /health, /, /config
├── frontend/             # Next.js 15 — app/page.tsx, app/api/stream (BFF)
│   ├── app/api/stream/   # Proxies SSE to FastAPI
│   ├── components/      # ArticleForm, ArticlePreview, MermaidBlock
│   └── lib/               # parseSse.ts, pipelineTypes.ts, pipelineStatus.ts
├── .env.example
├── pyproject.toml        # dependencies (managed by uv)
├── uv.lock               # pinned resolution — committed for reproducibility
└── README.md
```

## How OpenRouter plugs into the Agents SDK

OpenRouter implements the OpenAI Chat Completions API, so Swift registers
an `AsyncOpenAI` client with a custom `base_url`/`api_key` as the Agents
SDK's default client and forces `chat_completions` mode. See
`backend/agents/providers.py`. Agents can then be built with either a
plain model slug (e.g. `"anthropic/claude-sonnet-4.5"`) or the convenience
wrapper `openrouter_model(...)`.

## Configuration reference

Every setting can be overridden with an environment variable; see
`.env.example` for the full list. Defaults live in `backend/config.py`.

| Env var                        | Default                             | Purpose                       |
| ------------------------------ | ----------------------------------- | ----------------------------- |
| `OPENROUTER_API_KEY`           | *(required)*                        | OpenRouter credential         |
| `OPENROUTER_BASE_URL`          | `https://openrouter.ai/api/v1`      | OpenRouter endpoint           |
| `SERPER_API_KEY`               | *(optional)*                        | Enables Evaluator Google search via [Serper](https://serper.dev) |
| `SWIFT_ORCHESTRATOR_MODEL`     | `anthropic/claude-sonnet-4.5`       | Orchestrator model slug       |
| `SWIFT_WRITER_MODEL`           | `openai/gpt-4o-mini`                | Writer model slug             |
| `SWIFT_EVALUATOR_MODEL`        | `openai/gpt-4o`                     | Evaluator model (fact-checks) |
| `SWIFT_IMAGE_AGENT_MODEL`      | `openai/gpt-4o-mini`                | Image-placeholder agent model |
| `SWIFT_ARTICLES_DIR`           | `articles`                          | Output folder                 |
| `SWIFT_CORS_ORIGINS`           | `http://localhost:3000`             | CORS allow-list (comma-sep)   |
| `SWIFT_WRITER_MCP_FETCH_ENABLED`   | `1`                             | Attach `mcp-server-fetch` to Writer    |
| `SWIFT_WRITER_MCP_SERVERS`         | `[]`                            | Extra MCP servers for Writer (JSON)    |
| `SWIFT_EVALUATOR_MCP_FETCH_ENABLED`| `1`                             | Attach `mcp-server-fetch` to Evaluator |
| `SWIFT_EVALUATOR_MCP_SERPER_ENABLED` | `1`                           | Attach Serper search to Evaluator (no-op without `SERPER_API_KEY`) |
| `SWIFT_EVALUATOR_MCP_SERVERS`      | `[]`                            | Extra MCP servers for Evaluator (JSON) |
| `SWIFT_ORCHESTRATOR_MAX_RETRIES`   | `3`                             | Max Writer revisions after the initial draft |
| `SWIFT_POLLINATIONS_MODEL`         | `flux`                          | Pollinations image model (`flux`, `turbo`, `flux-realism`, ...) |
| `SWIFT_POLLINATIONS_WIDTH`         | `1024`                          | Image width in pixels |
| `SWIFT_POLLINATIONS_HEIGHT`        | `1024`                          | Image height in pixels |
| `SWIFT_POLLINATIONS_ENHANCE`       | `true`                          | Ask Pollinations to rewrite short prompts server-side |
| `SWIFT_POLLINATIONS_SEED`          | *(random)*                      | Fix an int for deterministic URLs in tests |
| `SWIFT_POLLINATIONS_REFERRER`      | `swift-writer`                  | App identifier sent to Pollinations |
| `SWIFT_MCP_SERVER_ENABLED`         | `true`                          | Mount the FastMCP surface into FastAPI |
| `SWIFT_MCP_SERVER_MOUNT_PATH`      | `/mcp`                          | Path prefix for the MCP HTTP transport |
| `SWIFT_MCP_SERVER_BEARER_TOKEN`    | *(open)*                        | Required bearer token for MCP HTTP requests; unset = no auth (localhost only) |
| `SWIFT_SSE_KEEP_ALIVE_SECONDS`     | `15`                            | SSE keep-alive comment interval for `/api/generate/stream` |
| `NEXT_PUBLIC_API_URL`              | `http://127.0.0.1:8000`         | Set in `frontend/.env.local` — FastAPI base URL for the Next.js SSE proxy |
