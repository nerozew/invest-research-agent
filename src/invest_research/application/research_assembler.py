"""P06-11E：确定性研究包组装器（ResearchPackAssembler）。

数据流（LLM 选择，代码组装，P06-11E）：

    Agent 工具循环
    → 独立 JSON Finalizer
    → BoundaryCanonicalizer
    → Pydantic (ResearchSelectionDraft)
    → ResearchPackAssembler
    → ResearchPack

职责：
- ``assemble``：接收 ``ResearchSelectionDraft``（只含 ``selected_source_urls``），
  从原始可信来源集合中确定性取回完整 ``Source``，组装出 ``ResearchPack``。

为什么需要本服务：
- Research Pack 的 ``Sources`` 嵌套契约（``canonical_url`` / ``source_type`` /
  ``accessed_at`` 必填）容易被 DeepSeek JSON 文本路径丢失；
- LLM 只表达"选择了哪些来源 URL"，真正的 ``Source`` 由本地代码构造，
  LLM 不具备发明来源/篡改 URL 的能力。

确定性来源构造：
- 输入 ``source_filings``：可信 SEC 申报记录列表（来自预取/缓存，
  每条含 ``primary_document_url`` / ``form_type`` / ``filing_date``）；
- ``selected_source_urls`` 中的 URL 必须命中 ``primary_document_url`` 才被接受；
- 未声明/未命中的 URL 一律忽略（不猜测、不发明）；
- 无任何匹配来源 → ``ResearchAssemblerError``（禁止生成伪造 ResearchPack）。

依赖方向：application → domain（Pydantic 模型）+ 标准库。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import (
    CompanyIdentity,
    ResearchPack,
    ResearchRequest,
    ResearchSelectionDraft,
    Source,
    SourceType,
)


class ResearchAssemblerError(RuntimeError):
    """组装器确定性失败（携带稳定错误码与失败阶段 02_research）。"""

    error_code: str
    failure_stage: str = "02_research"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class ResearchPackAssembler:
    """确定性组装器：ResearchSelectionDraft → ResearchPack（不调用 LLM）。"""

    def assemble(
        self,
        draft: ResearchSelectionDraft,
        *,
        request: ResearchRequest,
        company_identity: CompanyIdentity,
        source_filings: list[dict[str, Any]],
    ) -> ResearchPack:
        """把选择草稿 + 可信 SEC 申报记录组装为最终 ResearchPack。

        参数：
        - ``draft``：LLM 输出的选择草稿（只含 selected_source_urls）；
        - ``request``：ResearchRequest（as_of_date / requested_forms）；
        - ``company_identity``：可信公司身份（来自预取/解析）；
        - ``source_filings``：可信 SEC 申报记录列表（来自预取/缓存），
          每条必须含 ``primary_document_url`` / ``form_type`` / ``filing_date``。
        """
        # 1) 构造 canonical_url → 申报记录索引（确定性去重保序）
        index: dict[str, dict[str, Any]] = {}
        for filing_raw in source_filings:
            if not isinstance(filing_raw, dict):
                continue
            filing: dict[str, Any] = filing_raw
            url = filing.get("primary_document_url")
            if not url or not isinstance(url, str) or not url.strip():
                continue
            index.setdefault(url.strip(), filing)

        # 2) 按草稿选择 URL 构造 Source（只接受命中可信来源的 URL）
        sources: list[Source] = []
        seen: set[str] = set()
        for url in draft.selected_source_urls:
            if url in seen:
                continue
            seen.add(url)
            found: dict[str, Any] | None = index.get(url)
            if found is None:
                # 未命中的 URL：不猜测、不发明。忽略（不报错，无法证明其可信性）。
                continue
            form_type = str(found.get("form_type") or "SEC")
            filing_date = found.get("filing_date")
            sources.append(
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url=url,
                    title=f"{form_type} filed {filing_date}",
                    published_at=(
                        date.fromisoformat(str(filing_date)) if filing_date else request.as_of_date
                    ),
                    accessed_at=request.as_of_date,
                    # P05.5-fix：表单类型作确定性 locator（P05-13 验收要求 SEC 来源带 locator）
                    locator=form_type,
                )
            )

        # 3) 无有效来源 → 明确失败（禁止生成伪造 ResearchPack）
        if not sources:
            raise ResearchAssemblerError(
                ErrorCode.SCHEMA_INVALID.value,
                "Research 选择草稿没有命中任何可信 SEC 申报来源，"
                "禁止生成包含伪造来源的 ResearchPack",
            )

        return ResearchPack(
            version="research_pack_v1",
            company_identity=company_identity,
            as_of_date=request.as_of_date,
            sources=sources,
            coverage_notes=draft.coverage_notes,
            conflicts=list(draft.conflicts),
        )
