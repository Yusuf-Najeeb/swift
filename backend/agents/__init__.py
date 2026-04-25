"""Swift agents package.

Exports the OpenRouter provider wiring plus the shipped agent builders
and orchestration entry points:

* Step 2 — Writer
* Step 3 — Evaluator
* Step 4 — Orchestrator
* Step 5 — Image agent
* Step 6 — FastMCP server
* Step 7 — Pipeline events       (current)
"""

from backend.agents.evaluator import (
    APPROVAL_THRESHOLD,
    EVALUATOR_AGENT_NAME,
    EVALUATOR_INSTRUCTIONS,
    build_evaluator_agent,
)
from backend.agents.events import (
    AttemptStartedEvent,
    EvaluatorCompletedEvent,
    EventCallback,
    ImagesCompletedEvent,
    ImagesStartedEvent,
    PipelineEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    WriterCompletedEvent,
)
from backend.agents.image_agent import (
    build_image_url,
    extract_diagrams,
    illustrate_article,
)
from backend.agents.mcp_clients import (
    DEFAULT_EVALUATOR_FETCH_SPEC,
    DEFAULT_WRITER_FETCH_SPEC,
    SERPER_MCP_PACKAGE,
    build_evaluator_mcp_servers,
    build_mcp_server,
    build_serper_spec,
    build_writer_mcp_servers,
    evaluator_mcp_specs,
    safe_cleanup_mcp_servers,
    writer_mcp_specs,
)
from backend.agents.orchestrator import (
    RunEvaluator,
    RunWriter,
    orchestrate_article,
    run_revision_loop,
)
from backend.agents.providers import (
    configure_openrouter,
    openrouter_model,
)
from backend.agents.schemas import (
    ArticleBrief,
    ArticleLength,
    ArticleRun,
    DiagramAsset,
    EvaluatorFeedback,
    FinalArticle,
    ImageAsset,
    RevisionAttempt,
    WriterOutput,
)
from backend.agents.writer import (
    WRITER_AGENT_NAME,
    WRITER_INSTRUCTIONS,
    build_writer_agent,
)
from backend.config import MCPServerSpec, MCPTransport

__all__ = [
    # Provider wiring
    "configure_openrouter",
    "openrouter_model",
    # Core schemas
    "APPROVAL_THRESHOLD",
    "ArticleBrief",
    "ArticleLength",
    "ArticleRun",
    "DiagramAsset",
    "EvaluatorFeedback",
    "FinalArticle",
    "ImageAsset",
    "MCPServerSpec",
    "MCPTransport",
    "RevisionAttempt",
    "WriterOutput",
    # Writer (Step 2)
    "WRITER_AGENT_NAME",
    "WRITER_INSTRUCTIONS",
    "build_writer_agent",
    # Evaluator (Step 3)
    "EVALUATOR_AGENT_NAME",
    "EVALUATOR_INSTRUCTIONS",
    "build_evaluator_agent",
    # MCP client plumbing
    "DEFAULT_EVALUATOR_FETCH_SPEC",
    "DEFAULT_WRITER_FETCH_SPEC",
    "SERPER_MCP_PACKAGE",
    "build_evaluator_mcp_servers",
    "build_mcp_server",
    "build_serper_spec",
    "build_writer_mcp_servers",
    "evaluator_mcp_specs",
    "safe_cleanup_mcp_servers",
    "writer_mcp_specs",
    # Orchestrator (Step 4)
    "RunEvaluator",
    "RunWriter",
    "orchestrate_article",
    "run_revision_loop",
    # Image Agent (Step 5)
    "build_image_url",
    "extract_diagrams",
    "illustrate_article",
    # Pipeline events (Step 7)
    "AttemptStartedEvent",
    "EvaluatorCompletedEvent",
    "EventCallback",
    "ImagesCompletedEvent",
    "ImagesStartedEvent",
    "PipelineEvent",
    "RunCompletedEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "WriterCompletedEvent",
]
