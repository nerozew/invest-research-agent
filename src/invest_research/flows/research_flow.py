"""P03-11/12 实现 00-05 Flow 步骤（CrewAI Flow，fake 逻辑，不联网）。

目标（docs/05 P03-11、P03-12、docs/04 §2 步骤 00-05）：
- step00 接收/校验请求 → state.request；
- step01 公司解析（fake）→ state.company_identity；
- step02 信息搜集 Agent（fake，产出 ResearchPack）→ state.research_pack；
- step03 文档下载+解析清单（fake）→ state.document_manifest；
- step04 财报分析 Agent（fake，产出 FinancialAnalysisPack）→ state.analysis_pack；
- step05 报告撰写 Agent（fake，产出 ReportDraft）→ state.report_draft；
- 用 Flow 的 @start/@listen 编排顺序，验证"上游输出喂下游、state 逐步推进"。

P06-06B：``run_fake`` 可选注入 ``job_id`` / ``progress``（ProgressSink 端口），
在真实步骤边界标记 01-07 步骤状态；不注入时行为与旧版完全一致。
进度写入失败绝不中断任务（尽力而为，脱敏日志由调用方负责）。

P03-12 目标（"接入 sequential Crew 形成 04-05 步骤"）：
- 演示 Flow 如何收纳 Agent 产物（analysis_pack / report_draft 落进 state）；
- 真实 Crew 嵌入（P03-08 的 build_research_crew 全链）与端到端统一在 P03-15 验证；
- 本任务用 fake 直接构造 pack / draft，形成 00-05 线性链，为后续真实接入留出 step04/05 停靠点。
"""

from __future__ import annotations

import uuid

from crewai.flow.flow import Flow, listen, start

from invest_research.application.progress import ProgressSink
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.flows.manifest import build_run_manifest
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.state import ResearchFlowState


class ResearchFlow(Flow[ResearchFlowState]):
    """研究任务核心 Flow：00-07 步骤（fake 逻辑）。

    ``_progress`` / ``_job_id`` 由 ``run_fake`` 注入（可选）：提供时在
    真实步骤边界标记进度（P06-06B），不传则行为与旧版完全一致。
    """

    def __init__(self) -> None:
        super().__init__()
        self._progress: ProgressSink | None = None
        self._job_id: uuid.UUID | None = None

    def _mark_running(self, step: str) -> None:
        """步骤开始钩子（progress 未注入时静默跳过；写入失败不中断任务）。"""
        if self._progress is None or self._job_id is None:
            return
        try:
            self._progress.mark_step_running(self._job_id, step)
        except Exception:
            # 进度写入尽力而为：任何失败都不得中断任务（脱敏日志由调用方负责）
            pass

    def _mark_succeeded(self, step: str) -> None:
        """步骤成功钩子。"""
        if self._progress is None or self._job_id is None:
            return
        try:
            self._progress.mark_step_succeeded(self._job_id, step)
        except Exception:
            pass

    @start()
    def step00_receive_request(self) -> None:
        """步骤 00：接收并校验请求。

        真实请求由 API/CLI 传入；此处用 state 里预置的 request 占位。
        P04 接入端点后改为从入参接收。
        """
        request = self.state.request
        if request is None:
            raise ValueError("缺少 ResearchRequest：任务必须在 step00 前提供请求")
        # 模型校验已在 ResearchRequest 构造时完成（生成即合法）
        self.state.request = request
        # P06-06B：00_request 由 Worker 开始处理时标记 running（幂等创建在 Worker 侧），
        # 此处标记成功（请求校验通过）。
        self._mark_succeeded("00_request")

    @listen(step00_receive_request)
    def step01_resolve_company(self) -> None:
        """步骤 01：公司解析。

        fake：直接把请求里的公司名映射为假定 CIK/legal_name（不再请求真实 SEC）。
        真实逻辑在 P02-04 CompanyResolverTool（后续接入）。
        """
        self._mark_running("01_company_resolve")
        request = self.state.request
        assert request is not None  # step00 已保证
        cik = "0000789019" if request.input_company.upper() == "MSFT" else "0000000000"
        self.state.company_identity = CompanyIdentity(
            cik=cik, legal_name=f"{request.input_company} Corp"
        )
        self._mark_succeeded("01_company_resolve")

    @listen(step01_resolve_company)
    def step02_run_research_agent(self) -> None:
        """步骤 02：信息搜集 Agent → ResearchPack。

        fake：这里直接构造一个最小的 ResearchPack（真实流程由 P03-08 的 Crew 或
        单独 research agent 产出；本任务演示 Flow 如何收纳 Agent 产物）。
        """
        self._mark_running("02_research")
        identity = self.state.company_identity
        request = self.state.request
        assert identity is not None and request is not None
        # sources 至少 1 条（领域契约 min_length=1）；fake 给一条占位来源
        self.state.research_pack = ResearchPack(
            version="research_pack_v1",
            company_identity=identity,
            as_of_date=request.as_of_date,
            sources=[
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url="https://fake.example/10k",
                    title="Latest 10-K",
                    accessed_at=request.as_of_date,
                )
            ],
        )
        self._mark_succeeded("02_research")

    @listen(step02_run_research_agent)
    def step03_collect_documents(self) -> None:
        """步骤 03：文档下载与解析清单。

        fake：返回假清单（真实工具：P02-05 SECSubmissions + P02-08 Downloader +
        P02-09/10 Parser，后续接入）。
        """
        self._mark_running("03_documents")
        self.state.document_manifest = {
            "documents": [
                {"filing": "10-K", "status": "parsed", "checksum": "fake-abc"},
                {"filing": "10-Q", "status": "parsed", "checksum": "fake-def"},
            ]
        }
        self._mark_succeeded("03_documents")

    @listen(step03_collect_documents)
    def step04_run_analysis_agent(self) -> None:
        """步骤 04：财报分析 Agent → FinancialAnalysisPack。

        fake：这里直接构造一个最小的分析 pack（真实流程由 P03-08 的 sequential
        Crew 或单独 analysis agent 产出；本任务演示 Flow 如何收纳 Agent 产物）。
        """
        self._mark_running("04_analysis")
        identity = self.state.company_identity
        request = self.state.request
        assert identity is not None and request is not None
        # facts 至少 1 条（领域契约 min_length=1）；fake 给一条占位收入事实
        self.state.analysis_pack = FinancialAnalysisPack(
            version="analysis_pack_v1",
            period_end=request.as_of_date,
            facts=[
                FinancialFact(
                    company_id=identity.cik,
                    source_id="fake-source",
                    taxonomy="us-gaap",
                    concept="Revenue",
                    value=100000000000,
                    unit="USD",
                    period_start=request.as_of_date.replace(year=request.as_of_date.year - 1),
                    period_end=request.as_of_date,
                )
            ],
            analysis_notes="fake 分析占位",
        )
        self._mark_succeeded("04_analysis")

    @listen(step04_run_analysis_agent)
    def step05_run_writer_agent(self) -> None:
        """步骤 05：报告撰写 Agent → ReportDraft。

        fake：这里直接构造一个最小的草稿（真实流程由 P03-08 的 sequential Crew
        或单独 writer agent 产出；本任务演示 Flow 如何收纳 Agent 产物）。
        """
        self._mark_running("05_writer")
        identity = self.state.company_identity
        assert identity is not None
        # fake 产出含 PRD §7 全部必需章节 + 引用键的合规草稿（端到端可发布）
        self.state.report_draft = ReportDraft(
            version="report_draft_v1",
            title=f"{identity.legal_name} 投资研究初稿",
            markdown=(
                f"# {identity.legal_name}\n\n"
                "## 执行摘要\n执行摘要内容\n"
                "## 公司与业务概览\n公司与业务概览内容\n"
                "## 近期重要事件与行业背景\n近期重要事件与行业背景内容\n"
                "## 财务表现\n财务表现内容\n"
                "## 关键指标表\n关键指标表内容\n"
                "## 风险因素与催化因素\n风险因素与催化因素内容\n"
                "## 数据限制\n数据限制内容\n"
                "## 非投资建议声明\n非投资建议声明内容"
            ),
            citation_keys=["fake-claim-1"],
        )
        self._mark_succeeded("05_writer")

    @listen(step05_run_writer_agent)
    def step06_run_quality_gate(self) -> None:
        """步骤 06：质量门禁 → QualityReport。

        P03-13：对 00-05 产物运行确定性硬门禁（flows.quality.run_quality_gate），
        结果写入 state.quality_report。不通过时 recommendation=rejected（P03-14 发布读取）。
        """
        self._mark_running("06_quality_gate")
        self.state.quality_report = run_quality_gate(self.state)
        self._mark_succeeded("06_quality_gate")

    @listen(step06_run_quality_gate)
    def step07_publish_manifest(self) -> None:
        """步骤 07：发布与 RunManifest。

        P03-14：质量门禁通过 → 生成 RunManifest 写入 state.run_manifest；
        不通过 → run_manifest.status=rejected（发布层据此不做最终发布）。
        模型名从 LLMConfig 读取（用测试占位 key，仅取模型名，不触发真实调用）。
        """
        self._mark_running("07_manifest")
        from invest_research.agents.llm_factory import LLMConfig
        from invest_research.settings import Settings

        settings = Settings(
            _env_file=None,
            llm_api_key="sk-manifest-placeholder",
            sec_user_agent_contact="test@example.com",
        )
        config = LLMConfig.from_settings(settings)
        self.state.run_manifest = build_run_manifest(self.state, config)
        self._mark_succeeded("07_manifest")

    def run_fake(
        self,
        request: ResearchRequest,
        *,
        job_id: uuid.UUID | None = None,
        progress: ProgressSink | None = None,
    ) -> ResearchFlowState:
        """便捷入口：注入请求并 kickoff，返回执行后的状态。

        P06-06B：``job_id`` / ``progress`` 可选注入；提供时在步骤边界标记实时进度。
        """
        self._job_id = job_id
        self._progress = progress
        self.state.request = request
        self.kickoff()
        return self.state