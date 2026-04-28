
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MCPTransport = Literal["stdio", "sse", "http"]


class MCPServerSpec(BaseModel):

    name: str = Field(..., description="Human-readable name used in logs and tool prefixes.")
    transport: MCPTransport = Field("stdio", description="Transport: stdio | sse | http.")

    command: Optional[str] = Field(None, description="Executable to launch for stdio transports.")
    args: List[str] = Field(default_factory=list, description="Arguments passed to the executable.")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment vars for the subprocess.")

    url: Optional[str] = Field(None, description="Base URL for http or sse transports.")
    headers: Dict[str, str] = Field(default_factory=dict, description="Extra request headers.")

    client_session_timeout_seconds: float = Field(
        30.0,
        gt=0,
        description=(
            "Per-request timeout used by the MCP client session. Bumped "
            "above the SDK default (5s) so cold-starts of stdio servers "
            "launched via `uvx` / `npx` — which may download packages on "
            "first run — don't spuriously fail."
        ),
    )


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Swift Writer"
    app_version: str = "0.1.0"

    openrouter_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "openrouter_api_key"),
        description="API key from https://openrouter.ai/keys",
    )
    openrouter_base_url: str = Field(
        "https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENROUTER_BASE_URL", "openrouter_base_url"),
    )

    serper_api_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("SERPER_API_KEY", "serper_api_key"),
        description=(
            "Optional credential for Serper (https://serper.dev). When "
            "set, the Evaluator's Serper MCP server (google_search + "
            "scrape) is activated — see evaluator_mcp_serper_enabled."
        ),
    )

    orchestrator_model: str = Field(
        "anthropic/claude-sonnet-4.5",
        validation_alias=AliasChoices("SWIFT_ORCHESTRATOR_MODEL", "orchestrator_model"),
    )
    writer_model: str = Field(
        "openai/gpt-4o-mini",
        validation_alias=AliasChoices("SWIFT_WRITER_MODEL", "writer_model"),
    )
    evaluator_model: str = Field(
        "openai/gpt-4o",
        validation_alias=AliasChoices("SWIFT_EVALUATOR_MODEL", "evaluator_model"),
    )
    image_agent_model: str = Field(
        "openai/gpt-4o-mini",
        validation_alias=AliasChoices("SWIFT_IMAGE_AGENT_MODEL", "image_agent_model"),
    )

    articles_dir: str = Field(
        "articles",
        validation_alias=AliasChoices("SWIFT_ARTICLES_DIR", "articles_dir"),
    )

    azure_storage_connection_string: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONNECTION_STRING",
            "azure_storage_connection_string",
        ),
        description=(
            "If set, saved articles use Azure Blob; otherwise the local "
            "``articles_dir`` on disk (Compose / dev)."
        ),
    )
    azure_storage_container_name: str = Field(
        "articles",
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONTAINER_NAME",
            "azure_storage_container_name",
        ),
        description="Blob container name when using ``azure_storage_connection_string``.",
    )

    api_bearer_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "SWIFT_API_BEARER_TOKEN",
            "api_bearer_token",
        ),
        description=(
            "If set, protects the article pipeline, article file APIs, and "
            "GET /config. Omit for local-only dev."
        ),
    )

    cors_origins: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        validation_alias=AliasChoices("SWIFT_CORS_ORIGINS", "cors_origins"),
    )

    writer_mcp_fetch_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "SWIFT_WRITER_MCP_FETCH_ENABLED",
            "writer_mcp_fetch_enabled",
        ),
        description=(
            "When true, the Writer agent is attached to the reference "
            "mcp-server-fetch (launched via `uvx`) so it can resolve URLs."
        ),
    )
    writer_mcp_servers: List[MCPServerSpec] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "SWIFT_WRITER_MCP_SERVERS",
            "writer_mcp_servers",
        ),
        description=(
            "Additional MCP servers to attach to the Writer. Supply as a "
            "JSON list of MCPServerSpec objects via the env var."
        ),
    )

    evaluator_mcp_fetch_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "SWIFT_EVALUATOR_MCP_FETCH_ENABLED",
            "evaluator_mcp_fetch_enabled",
        ),
        description=(
            "When true, the Evaluator is attached to mcp-server-fetch "
            "so it can resolve URLs the Writer cited inline and verify "
            "claims against the source."
        ),
    )
    evaluator_mcp_serper_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "SWIFT_EVALUATOR_MCP_SERPER_ENABLED",
            "evaluator_mcp_serper_enabled",
        ),
        description=(
            "When true AND ``serper_api_key`` is set, the Evaluator is "
            "attached to serper-search-scrape-mcp-server (Google search "
            "+ page scrape) so it can look up suspicious claims that "
            "aren't already backed by a URL in the draft. Silently "
            "skipped if no key is present — so the default is safe."
        ),
    )
    evaluator_mcp_servers: List[MCPServerSpec] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "SWIFT_EVALUATOR_MCP_SERVERS",
            "evaluator_mcp_servers",
        ),
        description=(
            "Additional MCP servers to attach to the Evaluator beyond "
            "the built-in fetch + Serper (e.g. Brave/Tavily search, "
            "private knowledge bases). JSON list of MCPServerSpec "
            "objects."
        ),
    )

    orchestrator_max_retries: int = Field(
        3,
        ge=0,
        validation_alias=AliasChoices(
            "SWIFT_ORCHESTRATOR_MAX_RETRIES",
            "orchestrator_max_retries",
        ),
        description=(
            "How many revision attempts the Orchestrator will make "
            "after the initial draft before giving up. Total attempts "
            "per article = 1 (initial) + orchestrator_max_retries. "
            "The canonical plan calls for 3, so up to 4 attempts."
        ),
    )

    pollinations_base_url: str = Field(
        "https://image.pollinations.ai/prompt",
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_BASE_URL",
            "pollinations_base_url",
        ),
        description="Pollinations prompt endpoint (trailing slash stripped).",
    )
    pollinations_model: str = Field(
        "flux",
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_MODEL",
            "pollinations_model",
        ),
        description=(
            "Pollinations image model. All of these are known-reachable "
            "on the anonymous tier (probed 2026-04):\n\n"
            "* ``flux``          — default. Good generalist; works well "
            "  with editorial-illustration / isometric style hints.\n"
            "* ``flux-realism``  — tuned for photoreal output; best for "
            "  articles that want stock-photo-style visuals.\n"
            "* ``turbo``         — faster, lower fidelity. Useful for "
            "  drafting or high-volume generation.\n"
            "* ``sana``          — newer model Pollinations advertises "
            "  via ``/models``; quality varies by topic.\n\n"
            "The Writer's prompt matters more than the model choice — "
            "see WRITER_INSTRUCTIONS for art-direction rules."
        ),
    )
    pollinations_width: int = Field(
        1024,
        gt=0,
        le=4096,
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_WIDTH",
            "pollinations_width",
        ),
        description="Image width in pixels.",
    )
    pollinations_height: int = Field(
        1024,
        gt=0,
        le=4096,
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_HEIGHT",
            "pollinations_height",
        ),
        description="Image height in pixels.",
    )
    pollinations_enhance: bool = Field(
        True,
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_ENHANCE",
            "pollinations_enhance",
        ),
        description=(
            "Ask Pollinations to enhance short prompts server-side "
            "before generating. Pollinations' default is ``false``, "
            "but we default to ``true`` because the Writer only "
            "supplies one-line descriptions and enhancement costs us "
            "nothing."
        ),
    )
    pollinations_nologo: bool = Field(
        False,
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_NOLOGO",
            "pollinations_nologo",
        ),
        description=(
            "Strip the Pollinations watermark from generated images. "
            "Requires an authenticated account — silently ignored on "
            "the anonymous tier."
        ),
    )
    pollinations_seed: Optional[int] = Field(
        None,
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_SEED",
            "pollinations_seed",
        ),
        description=(
            "Fixed seed for deterministic image generation — set this "
            "in tests so URLs stay stable across runs. ``None`` means "
            "Pollinations picks a random seed per request."
        ),
    )
    pollinations_referrer: Optional[str] = Field(
        "swift-writer",
        validation_alias=AliasChoices(
            "SWIFT_POLLINATIONS_REFERRER",
            "pollinations_referrer",
        ),
        description=(
            "Identifies Swift to Pollinations — the web-app-safe auth "
            "mechanism per their docs. Used for abuse-mitigation and "
            "request-routing. Set to an empty string to omit.\n\n"
            "We deliberately don't support bearer tokens here: the "
            "Image Agent's only job is to build URLs that end users' "
            "browsers will later fetch. Browsers can't inject "
            "``Authorization`` headers for ``<img src=...>`` loads, so "
            "a token would never reach Pollinations. If you need "
            "higher rate limits on server-rendered previews, fetch "
            "those yourself with a bearer token in a separate step."
        ),
    )

    mcp_server_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "SWIFT_MCP_SERVER_ENABLED",
            "mcp_server_enabled",
        ),
        description=(
            "When true, the FastMCP server is mounted into the FastAPI "
            "app at ``mcp_server_mount_path`` on startup. Set to false "
            "to run the REST API without the MCP surface — useful for "
            "constrained environments or when Swift is only used as a "
            "Python library. The stdio entrypoint "
            "(``python -m backend.mcp.server``) is independent of this "
            "flag since it bypasses the FastAPI app entirely."
        ),
    )
    mcp_server_mount_path: str = Field(
        "/mcp",
        validation_alias=AliasChoices(
            "SWIFT_MCP_SERVER_MOUNT_PATH",
            "mcp_server_mount_path",
        ),
        description=(
            "Path prefix at which the FastMCP Streamable-HTTP transport "
            "is exposed. Clients will POST MCP JSON-RPC messages to "
            "``<base_url><mcp_server_mount_path>``. Must start with "
            "``/``; no trailing slash."
        ),
    )
    mcp_server_bearer_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "SWIFT_MCP_SERVER_BEARER_TOKEN",
            "mcp_server_bearer_token",
        ),
        description=(
            "Optional bearer token required on the ``Authorization`` "
            "header to reach the MCP HTTP transport. Unset → open "
            "(fine for localhost; NOT fine for public deployments). "
            "Set → all MCP HTTP requests must include "
            "``Authorization: Bearer <token>``. The stdio entrypoint "
            "ignores this setting — it trusts the OS process boundary."
        ),
    )

    sse_keep_alive_seconds: float = Field(
        15.0,
        gt=0,
        validation_alias=AliasChoices(
            "SWIFT_SSE_KEEP_ALIVE_SECONDS",
            "sse_keep_alive_seconds",
        ),
        description=(
            "Interval (seconds) between SSE keep-alive comments on the "
            "``/api/generate/stream`` endpoint. Browsers will close "
            "idle EventSource connections after roughly 30s; a "
            "keep-alive every 15s is the standard belt-and-braces "
            "choice. Pipelines frequently go >30s between genuine "
            "events (Writer drafting a long article), so without this "
            "the connection would drop mid-run and the UI would show "
            "a stale spinner."
        ),
    )

    @field_validator("mcp_server_mount_path", mode="after")
    @classmethod
    def _validate_mount_path(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith("/"):
            raise ValueError("mcp_server_mount_path must start with '/'")
        if stripped != "/" and stripped.endswith("/"):
            stripped = stripped.rstrip("/")
        return stripped

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value):
        if isinstance(value, str):
            if value.strip().startswith("["):
                return value
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:

    return Settings()  # type: ignore[call-arg]
