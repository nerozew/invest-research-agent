"""P03-10 typed Flow state（CrewAI Flow 的状态模型，Pydantic）。

用途（docs/05 P03-10、docs/04 §2 步骤契约）：
- Flow 用 ``Flow[ResearchFlowState]`` 携带跨步骤状态；
- 本模型覆盖工作流 00-07 各阶段需在 Flow 层共享的数据：
  请求、公司身份、三个 Agent pack、文档清单、质量报告、run manifest；
- typed（Pydantic）：字段类型校验 + IDE 自动补全 + 序列化/恢复（Pydantic）。

注意：本任务只建**状态模型**，不含 Flow 方法与 @start/@listen 编排（那是 P03-11+）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    QualityReport,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
)


class ResearchFlowState(BaseModel):
    """跨 Flow 步骤共享的研究任务状态（对齐 docs/04 §2 步骤输出）。"""

    model_config = ConfigDict(frozen=False)  # Flow 运行期需要可改（self.state.x = ...）

    # 步骤 00：请求
    request: ResearchRequest | None = None

    # 步骤 01：公司解析
    company_identity: CompanyIdentity | None = None

    # 步骤 02：信息搜集 Agent → ResearchPack
    research_pack: ResearchPack | None = None

    # 步骤 03：文档清单（下载+解析结果，P03-11 起填充）
    document_manifest: dict[str, object] = {}

    # 步骤 04：财报分析 Agent → FinancialAnalysisPack
    analysis_pack: FinancialAnalysisPack | None = None

    # 步骤 05：报告撰写 Agent → ReportDraft
    report_draft: ReportDraft | None = None

    # 步骤 06：质量门禁
    quality_report: QualityReport | None = None

    # 步骤 07：发布 manifest 摘要（由 P03-14 补全版本/耗时/模型等）
    run_manifest: dict[str, object] = {}
