"""P03-18 Writer 定向修订 Task 测试（fake LLM，不联网）。

验证目标（docs/05 P03-18）：
- 修订 Agent 白名单与 Writer 一致（ArtifactReader/CitationVerifier/TemplateGuide，无搜索/计算）；
- build_revision_task：problems 注入 description、output_pydantic=ReportDraft；
- 空 issues / revision_number<1 抛 ValueError；
- fake LLM 可实例化 ReportDraft（定向修订产出结构化草稿）。
"""

from __future__ import annotations

import pytest
from crewai import Agent, Task

from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.agents.revision_task import (
    REVISION_PROMPT_V1,
    build_revision_agent,
    build_revision_pair,
    build_revision_task,
)
from invest_research.domain.models import ReportDraft
from invest_research.domain.quality import QualityAction, QualityIssue, QualitySeverity
from invest_research.settings import Settings


def _config() -> LLMConfig:
    s = Settings(_env_file=None, llm_api_key="sk-test", sec_user_agent_contact="t@e.com")
    return LLMConfig.from_settings(s)


def _fake() -> FakeLLM:
    draft = ReportDraft(
        version="report_draft_v1",
        title="微软修订稿",
        markdown="## 执行摘要\n\n## 财务表现\n\n## 非投资建议",
        citation_keys=["c1"],
    )
    return FakeLLM(config=_config(), role=LLMRole.WRITER, responses=[draft])


def _issues() -> list[QualityIssue]:
    return [
        QualityIssue(
            code="missing_section",
            severity=QualitySeverity.ERROR,
            stage="writer",
            message="报告缺少执行摘要章节",
            action=QualityAction.REVISE_REPORT,
        )
    ]


def test_revision_agent_uses_writer_whitelist() -> None:
    agent = build_revision_agent(_config(), fake=_fake())
    assert isinstance(agent, Agent)
    tool_names = {t.name for t in agent.tools or []}
    assert tool_names == {"ArtifactReader", "CitationVerifier", "TemplateGuide"}
    assert "GoogleSearch" not in tool_names and "FinancialCalculator" not in tool_names
    assert REVISION_PROMPT_V1 in agent.backstory


def test_revision_task_binds_report_draft() -> None:
    task = build_revision_task(
        _config(),
        _fake(),
        issues=_issues(),
        original_draft_version="report_draft_v1",
        revision_number=1,
    )
    assert isinstance(task, Task)
    assert task.output_pydantic is ReportDraft
    assert "第 1 次定向修订" in task.description
    assert "missing_section" in task.description
    assert "report_draft_v1" in task.description


def test_revision_task_rejects_empty_issues() -> None:
    with pytest.raises(ValueError):
        build_revision_task(
            _config(), _fake(), issues=[], original_draft_version="v1", revision_number=1
        )


def test_revision_task_rejects_zero_revision() -> None:
    with pytest.raises(ValueError):
        build_revision_task(
            _config(), _fake(), issues=_issues(), original_draft_version="v1", revision_number=0
        )


def test_revision_pair_same_agent() -> None:
    agent, task = build_revision_pair(
        _config(), _fake(), issues=_issues(), original_draft_version="v1", revision_number=1
    )
    assert task.agent is agent
