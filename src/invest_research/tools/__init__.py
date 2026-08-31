"""确定性工具层包（P02-01）。

依赖边界：本层只依赖标准库、Pydantic 与 domain 层；
禁止导入 CrewAI、FastAPI、SQLAlchemy 或任何供应商 SDK。

``tools/base.py`` 定义统一 Tool 契约（Protocol）与统一成功/失败结果对象，
后续工具（P02-02～P02-19）都实现该契约。
"""

from __future__ import annotations

from invest_research.tools.artifact_store import (
    ArtifactOperationResponse,
    ArtifactRef,
    ArtifactStore,
    ArtifactStoreRequest,
    ArtifactStoreTool,
)
from invest_research.tools.base import Tool, ToolError, ToolFailure, ToolResult, ToolSuccess
from invest_research.tools.citation_verifier import (
    CitationCheckRequest,
    CitationCheckResult,
    CitationSourceRef,
    CitationVerifierTool,
    verify_claim,
)
from invest_research.tools.company_resolver import (
    CompanyIndex,
    CompanyResolverTool,
    ResolveCompanyRequest,
    ResolveCompanyResponse,
)
from invest_research.tools.google_search import (
    GoogleSearchTool,
    SearchProvider,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from invest_research.tools.parser_router import (
    DocumentParseError,
    ParseOutcome,
    parse_document,
)
from invest_research.tools.sec_company_facts import (
    FetchFactsRequest,
    FetchFactsResponse,
    SECCompanyFactsTool,
)
from invest_research.tools.sec_submissions import (
    FetchSubmissionsRequest,
    FetchSubmissionsResponse,
    SECSubmissionsTool,
)
from invest_research.tools.serper_adapter import SERPER_ENDPOINT, SerperAdapter, SerperConfig

__all__ = [
    "Tool",
    "ToolError",
    "ToolFailure",
    "ToolResult",
    "ToolSuccess",
    "CompanyIndex",
    "CompanyResolverTool",
    "ResolveCompanyRequest",
    "ResolveCompanyResponse",
    "FetchFactsRequest",
    "FetchFactsResponse",
    "SECCompanyFactsTool",
    "FetchSubmissionsRequest",
    "FetchSubmissionsResponse",
    "SECSubmissionsTool",
    "DocumentParseError",
    "ParseOutcome",
    "parse_document",
    "ArtifactOperationResponse",
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactStoreRequest",
    "ArtifactStoreTool",
    "GoogleSearchTool",
    "SearchProvider",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "SerperAdapter",
    "SerperConfig",
    "SERPER_ENDPOINT",
    "CitationCheckRequest",
    "CitationCheckResult",
    "CitationSourceRef",
    "CitationVerifierTool",
    "verify_claim",
]
