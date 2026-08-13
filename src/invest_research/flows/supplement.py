"""P03-19 补充研究流程（deterministic supplement，fake 不联网）。

对齐 docs/04 §2.5 与 docs/06 §12.4.2（research_supplement_prompt_v1）：
- 只补充 SupplementResearchRequest.missing_evidence 指定的证据；
- 遵守 as_of_date；最多补证一次（attempt_number ≤ 1 次成功）；
- 不重做全部研究；不写分析结论（Analysis 的职责）。

实现：纯函数 ``apply_supplement``——把补证来源追加进 state.research_pack.sources
（ResearchPack frozen，经 model_copy 重建），消费一次 attempt 并返回新 state。
补证后的重新 Analysis → Writer → 质量门禁由 P03-20 ReflectionController 驱动。
"""

from __future__ import annotations

from invest_research.domain.models import Source
from invest_research.domain.quality import SupplementResearchRequest
from invest_research.flows.state import ResearchFlowState

# 补证最大次数（docs/04 §2.5：补充研究最多 1 次）
MAX_SUPPLEMENT_ATTEMPTS = 1


def apply_supplement(
    state: ResearchFlowState,
    request: SupplementResearchRequest,
) -> ResearchFlowState:
    """执行一次补证：向 research_pack 追加结构化来源，并推进 attempt。

    - 只有 research_pack 存在时才可补证（缺则补证无意义，抛 ValueError）；
    - attempt_number 超过 MAX_SUPPLEMENT_ATTEMPTS 时拒绝（最多一次）；
    - derived 来源 canonical_url 用 missing_evidence + as_of 生成确定性 fake URL
      （P03-19 阶段不联网；真实来源由 P04 搜索工具注入）。
    """
    if state.research_pack is None:
        raise ValueError("缺少 research_pack：补证必须先有研究基础")
    if request.attempt_number > MAX_SUPPLEMENT_ATTEMPTS:
        raise ValueError(
            f"补证最多 {MAX_SUPPLEMENT_ATTEMPTS} 次（当前 attempt={request.attempt_number}）"
        )

    # 构造补证来源（fake：可追溯占位 URL；真实工具接入后由外部返回真实来源）
    new_source = Source(
        source_type="sec_filing",  # P03-19 阶段用 SEC 类型占位（Pydantic 自动解析为 SourceType）
        canonical_url=f"https://supplement.example/{request.as_of_date.isoformat()}/{request.missing_evidence}",
        title=f"supplement: {request.missing_evidence}",
        accessed_at=request.as_of_date,
    )
    new_sources = [*state.research_pack.sources, new_source]
    state.research_pack = state.research_pack.model_copy(update={"sources": new_sources})
    return state
