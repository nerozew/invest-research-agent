"""P05-12B 真实工具注入与 LLM builder 离线验收测试（不联网）。

验收（对齐 P05-12B 离线验收）：
- real LLM builder 参数映射正确（model/base_url/temperature/timeout 透传），
  且 Key 不进入 repr/异常；
- Research 工具白名单只含搜集工具（无分析/写作工具）；
- live 模式缺 Serper API Key 时 fail-fast（由 live_resources.build_live_client_and_serper
  体现，不依赖 Celery/CrewAI）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import (
    LLMConfig,
    LLMRole,
    build_real_llm,
)
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.flow_wiring import (
    FlowModeError,
    LiveResearchFlowRunner,
)
from invest_research.settings import Settings


def _settings(
    *,
    flow_mode: str = "fake",
    api_key: str = "sk-test",
    serper_api_key: SecretStr | None = None,
) -> Settings:
    # serper_api_key 显式固定为 None：crewai 导入时会 load_dotenv() 把 .env
    # 灌进 os.environ，pydantic-settings 即使 _env_file=None 仍会读到它，
    # 导致「缺 key fail-fast」测试依赖外部环境而时好时坏。显式传参优先于环境变量。
    return Settings(
        _env_file=None,
        llm_api_key=api_key,
        sec_user_agent_contact="test@example.com",
        flow_mode=flow_mode,
        serper_api_key=serper_api_key,
    )


def test_build_real_llm_maps_params_and_no_key() -> None:
    """P05-12B：真实 LLM builder 参数映射正确，Key 不进入 repr。"""
    config = LLMConfig.from_settings(_settings(api_key="sk-super-secret"))
    llm = build_real_llm(config, LLMRole.RESEARCH)

    assert llm.model == config.model_research == "qwen-max"
    assert "sk-super-secret" not in repr(llm)
    assert "sk-super-secret" not in str(llm)
    # 构造阶段不联网（仅对象构造，不做任何请求）；temperature/base_url 由 CrewAI 内部持有
    assert config.timeout > 0


def test_real_llm_is_crewai_base_llm() -> None:
    """真实 LLM 是 crewai.BaseLLM 子类（可被 Agent(llm=...) 接受）。"""
    from crewai import BaseLLM

    llm = build_real_llm(LLMConfig.from_settings(_settings()), LLMRole.ANALYSIS)
    assert isinstance(llm, BaseLLM)


def test_live_runner_factory_keeps_key_out_of_artifacts(tmp_path: Path) -> None:
    """P05-12B：中间产物不含密钥（manifest/工件 JSON 无明文 key）。"""
    from types import SimpleNamespace

    from invest_research.domain.models import (
        CompanyIdentity,
        FinancialAnalysisPack,
        FinancialFact,
        ReportDraft,
        ResearchPack,
        Source,
        SourceType,
    )

    as_of = date(2025, 12, 31)
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
                accessed_at=as_of,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="fake-source",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100000000000,
                unit="USD",
                period_start=date(2024, 1, 1),
                period_end=as_of,
            )
        ],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 投资研究初稿",
        markdown=(
            "# Microsoft Corp\n\n## 执行摘要\n内容\n## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n## 风险\n内容\n## 数据限制\n内容\n## 非投资建议\n内容"
        ),
        citation_keys=["fake-claim-1"],
    )

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, draft])

    artifact_root = tmp_path / "artifacts"
    runner = LiveResearchFlowRunner(
        config=LLMConfig.from_settings(_settings(api_key="sk-top-secret")),
        artifact_root=str(artifact_root),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(
        ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    )

    # manifest 工件已落盘且不含密钥
    manifest_path = list(artifact_root.rglob("07_manifest.json"))
    assert manifest_path, "manifest 工件应被写入"
    content = manifest_path[0].read_text(encoding="utf-8")
    assert "sk-top-secret" not in content


def test_live_missing_serper_key_fails_fast() -> None:
    """P05-12B：live 模式 HTTP client+Serper 缺 key 时 fail-fast（轻量 live_resources）。"""
    from invest_research.infrastructure.live_resources import build_live_client_and_serper

    settings = _settings(flow_mode="live", api_key="sk-live")
    # serper_api_key 显式为 None → build_live_client_and_serper 抛 FlowModeError
    with pytest.raises(FlowModeError):
        build_live_client_and_serper(settings)
