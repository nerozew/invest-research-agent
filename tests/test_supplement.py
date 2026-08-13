"""P03-19 补充研究流程测试（纯函数，不联网）。

验证目标（docs/05 P03-19）：
- apply_supplement 只向 research_pack 追加来源，不改动其他字段；
- 遵守 as_of_date（来源 accessed_at 与请求一致）；
- attempt_number > MAX_SUPPLEMENT_ATTEMPTS 拒绝（最多一次）；
- research_pack 缺失时抛 ValueError。
"""

from __future__ import annotations

from datetime import date

import pytest

from invest_research.domain.models import (
    CompanyIdentity,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.domain.quality import SupplementResearchRequest
from invest_research.flows.state import ResearchFlowState
from invest_research.flows.supplement import MAX_SUPPLEMENT_ATTEMPTS, apply_supplement


def _state() -> ResearchFlowState:
    as_of = date(2025, 12, 31)
    pack = ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://e.com/10k",
                title="10-K",
                accessed_at=as_of,
            )
        ],
    )
    return ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        research_pack=pack,
    )


def _req(attempt: int = 0) -> SupplementResearchRequest:
    return SupplementResearchRequest(
        missing_evidence="缺少近三年收入数据",
        related_claim=None,
        required_source_type="sec_filing",
        as_of_date=date(2025, 12, 31),
        attempt_number=attempt,
    )


def test_supplement_appends_source_only() -> None:
    state = _state()
    before = len(state.research_pack.sources)  # type: ignore[union-attr]
    out = apply_supplement(state, _req())
    assert out.research_pack is not None
    assert len(out.research_pack.sources) == before + 1
    added = out.research_pack.sources[-1]
    assert "缺少近三年收入数据" in added.title
    assert added.accessed_at == date(2025, 12, 31)
    assert added.canonical_url.startswith("https://supplement.example/2025-12-31/")


def test_supplement_respects_as_of() -> None:
    out = apply_supplement(_state(), _req())
    assert out.research_pack is not None
    assert out.research_pack.sources[-1].accessed_at == date(2025, 12, 31)


def test_supplement_rejects_second_attempt() -> None:
    with pytest.raises(ValueError, match="最多"):
        apply_supplement(_state(), _req(attempt=MAX_SUPPLEMENT_ATTEMPTS + 1))


def test_supplement_requires_research_pack() -> None:
    state = _state()
    state.research_pack = None
    with pytest.raises(ValueError, match="research_pack"):
        apply_supplement(state, _req())
