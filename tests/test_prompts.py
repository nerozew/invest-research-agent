"""P03-02 信息搜集 Agent 提示词 v1 测试（版本化 prompt snapshot）。

验证目标（docs/05 P03-02 验收 + 提示词手册 §7 要求）：
- 提示词文件存在且可加载；
- 版本标识 `research_prompt_v1` 一致（PromptName/PROMPT_VERSION/文件内声明）；
- 角色与目标明确（研究员、收集整理而非下结论）；
- 工具白名单齐全（含最小权限：列出的工具就是信息搜集 Agent 的全部权限）；
- 输出契约指向 ResearchPack（结构化，不是自由文本）；
- 禁止项完整（不计算财务、不写投资建议、不用未来信息、不编造来源）；
- 提示词可被快照引用（sha256 稳定、可追踪、内容修改即变化）。

不联网、不修改数据库、不调用真实模型。
"""

from __future__ import annotations

from invest_research.prompts import (
    PROMPT_VERSION,
    PromptName,
    load_prompt,
    prompt_sha256,
)

# 关键段落断言用的子串（对应 research_prompt_v1.md 核心内容）
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "# 信息搜集 Agent 提示词 v1",
    "## 角色",
    "## 目标",
    "## 权威顺序",
    "## 允许工具",
    "## 输入",
    "## 输出契约",
    "## 规则",
    "## 禁止事项",
)

# 提示词内必须出现的关键短语（验收：输入/输出/禁止项齐全）
_REQUIRED_PHRASES: tuple[str, ...] = (
    "CompanyIdentity",
    "as_of_date",
    "ResearchPack",
    "research_pack_v1",
    "CompanyResolver",
    "WebSearch",
    "SECSubmissions",
    "SECCompanyFacts",
    "FilingDownloader",
    # P05.5-fix 新增硬规则
    "禁止自行选择其他截止日期",
    "Action/Action Input",
    "prefetch_summary",
    # 禁止项
    "禁止计算任何财务比率",
    "禁止撰写投资结论",
    "禁止编造来源",
)


def test_research_prompt_file_exists_and_loads() -> None:
    """提示词文件存在且可加载，内容非空。"""
    text = load_prompt(PromptName.RESEARCH)
    assert text.strip()  # 非空
    assert len(text) > 500  # 不是空壳，有实质内容


def test_research_prompt_contains_all_required_sections() -> None:
    """提示词包含全部必需的章节标签。"""
    text = load_prompt(PromptName.RESEARCH)
    for section in _REQUIRED_SECTIONS:
        assert section in text, f"缺少必需章节: {section}"


def test_research_prompt_contains_required_phrases() -> None:
    """提示词包含输入契约、输出契约、工具白名单与禁止项关键短语。"""
    text = load_prompt(PromptName.RESEARCH)
    for phrase in _REQUIRED_PHRASES:
        assert phrase in text, f"缺少关键短语: {phrase}"


def test_research_prompt_version_consistent() -> None:
    """PromptName / PROMPT_VERSION / 文件内版本声明三方一致。"""
    assert PromptName.RESEARCH.value == "research_prompt_v1"
    assert PROMPT_VERSION[PromptName.RESEARCH] == "research_prompt_v1"
    text = load_prompt(PromptName.RESEARCH)
    assert "> 版本：`research_prompt_v1`" in text


def test_analysis_prompt_file_exists_and_loads() -> None:
    """P03-03 / P06-09A：财报分析提示词 v2 存在且可加载，含核心禁止 LLM 算术约束。"""
    text = load_prompt(PromptName.ANALYSIS)
    assert text.strip()
    assert "# 财报分析 Agent 提示词 v2" in text
    assert "FinancialAnalysisPack" in text
    assert "schema_version" in text
    assert "analysis_pack_v2" in text
    # P06-09A：completeness 三态契约
    assert "completeness" in text
    assert "unavailable" in text
    assert "FinancialCalculator" in text
    # 红线：禁止 LLM 算术
    assert "禁止 LLM 算术" in text or "所有算术必须调用 FinancialCalculator" in text
    # 允许工具白名单最小化
    assert "ArtifactReader" in text
    assert "FinancialFactQuery" in text
    assert "FinancialCalculator" in text


def test_analysis_prompt_version_consistent() -> None:
    """PromptName / PROMPT_VERSION / 文件内版本声明三方一致（P06-09A）。"""
    assert PromptName.ANALYSIS.value == "analysis_prompt_v2"
    assert PROMPT_VERSION[PromptName.ANALYSIS] == "analysis_prompt_v2"
    assert "> 版本：`analysis_prompt_v2`" in load_prompt(PromptName.ANALYSIS)


def test_writer_prompt_file_exists_and_loads() -> None:
    """P03-04 / P06-09A：报告撰写提示词 v2 存在且可加载，含 grounded generation 约束。"""
    text = load_prompt(PromptName.WRITER)
    assert text.strip()
    assert "# 报告撰写 Agent 提示词 v2" in text
    assert "ReportDraft" in text
    assert "report_draft_v1" in text
    # P06-09A：按 completeness 状态组织报告（partial/unavailable 不编造）
    assert "completeness" in text
    assert "unavailable" in text
    assert "不得推断或编造任何财务数据" in text
    # 唯一输入：只有上游两个 pack
    assert "research_pack" in text
    assert "analysis_pack" in text
    # 禁止引入新事实（grounding 红线）
    assert "禁止引入新事实" in text or "禁止添加上游" in text
    # P06-11B：主 Writer 工具白名单收敛为一次聚合读取，
    # 避免多次确定性工具调用耗尽 max_iter。
    assert "WriterContextReader" in text
    assert "只调用一次" in text
    # 必备声明
    assert "非投资建议" in text


def test_writer_prompt_version_consistent() -> None:
    """PromptName / PROMPT_VERSION / 文件内版本声明三方一致（P06-09A）。"""
    assert PromptName.WRITER.value == "writer_prompt_v2"
    assert PROMPT_VERSION[PromptName.WRITER] == "writer_prompt_v2"
    assert "> 版本：`writer_prompt_v2`" in load_prompt(PromptName.WRITER)


def test_prompt_sha256_is_stable_and_content_sensitive() -> None:
    """snapshot：内容不变时 hash 稳定；内容一旦变化 hash 立即不同。"""
    sha = prompt_sha256(PromptName.RESEARCH)
    # 同一内容两次加载 hash 一致（可复现）
    assert sha == prompt_sha256(PromptName.RESEARCH)
    # hash 是 64 位 hex（sha256）
    assert len(sha) == 64
    # 内容敏感性验证：把内容改一个字，hash 必须变化
    text = load_prompt(PromptName.RESEARCH)
    changed = text.replace("审慎的公开信息研究员", "审慎的研究员", 1)
    assert hashlib_sha256(changed) != sha


def hashlib_sha256(content: str) -> str:
    """本地 sha256 帮助函数（与 loader.prompt_sha256 同算法）。"""
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
