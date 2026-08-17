"""P03-06 构建 Analysis Task（CrewAI 1.6.1；P05-12B 起支持统一 LLM 接口）。

目标（docs/05 P03-06 验收）：
- 组装财报分析 Agent（注入 ``AnyLLM``：FakeLLM 或 crewai.BaseLLM）与 ``Task``；
- Task 绑定 ``output_pydantic=FinancialAnalysisPack``；
- **只暴露允许的分析工具**（least-privilege）：FinancialFactQuery + FinancialCalculator，
  不暴露搜索/下载等无关工具；LLM 不自算，算术经确定性 FinancialCalculator 工具。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 的 AGENTS 模板）：
- ``@tool("Name")`` 装饰器把函数包成 CrewAI 工具；
- ``Agent(..., tools=[tool1, tool2])`` 注入工具白名单；
- 工具内部调用 ``financial/`` 确定性纯函数（Decimal / 版本化公式）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from crewai import Agent, Task
from crewai.tools import tool

from invest_research.agents.llm_factory import AnyLLM, LLMConfig, LLMRole, build_real_llm
from invest_research.domain.models import FinancialAnalysisPack
from invest_research.financial.concept_mapping import (
    ConceptMapping,
    load_concept_mapping,
    select_concept,
)
from invest_research.financial.metrics import (
    compute_gross_margin,
    compute_net_income_growth,
    compute_net_margin,
    compute_operating_margin,
    compute_revenue_growth,
)
from invest_research.financial.metrics_balance import (
    compute_asset_liability_ratio,
    compute_current_ratio,
    compute_free_cash_flow,
    compute_operating_cash_flow_ratio,
    compute_roa,
)
from invest_research.prompts.loader import PromptName, load_prompt
from invest_research.settings import ResearchProfile

_CONCEPTS_V1_PATH = (
    Path(__file__).resolve().parents[1] / "financial" / "mappings" / "concepts_v1.json"
)


@tool("FinancialFactQuery")
def financial_fact_query(
    concept: str,
    available_concepts: tuple[str, ...],
    prefer_annual: bool = True,
    period_end: date | None = None,
) -> dict[str, object]:
    """查询财务事实的优先 concept（确定性，不猜数）。

    - 按 versioned mapping（concepts_v1.json）的优先级，从 ``available_concepts``
      中选择第一个命中的 concept；
    - 返回 ``{"selected_concept": str | None}``；无匹配返回 None（禁止臆造，
      交由计算层 NOT_COMPUTABLE）。
    """
    mapping: ConceptMapping = load_concept_mapping(_CONCEPTS_V1_PATH)
    picked = select_concept(mapping, concept, available_concepts)
    return {"selected_concept": picked}


@tool("FinancialCalculator")
def financial_calculator(
    metric_name: str,
    job_id: str,
    period_end: date,
    **inputs: str,
) -> dict[str, object]:
    """确定性财务指标计算器（LLM 不自算，Decimal / 版本化公式）。

    支持指标（PRD §8）：revenue_growth / gross_margin / operating_margin /
    net_margin / net_income_growth / current_ratio / asset_liability_ratio /
    operating_cash_flow_ratio / free_cash_flow / roa。

    ``inputs`` 传字符串形式的数值（如 "100" / "-10"），工具内部转 Decimal 并计算，
    返回 ``MetricResult.model_dump()``（含 formula_version / inputs_json / status）。
    """
    # 允许的最大白名单：任何不在 PRD §8 的指标名 → 明确拒绝（不静默返回）
    _ALLOWED_METRICS = frozenset(
        {
            "revenue_growth",
            "gross_margin",
            "operating_margin",
            "net_margin",
            "net_income_growth",
            "current_ratio",
            "asset_liability_ratio",
            "operating_cash_flow_ratio",
            "free_cash_flow",
            "roa",
        }
    )
    if metric_name not in _ALLOWED_METRICS:
        return {
            "status": "not_computable",
            "reason": f"不支持的指标: {metric_name}（FinancialCalculator 仅支持 PRD §8 白名单）",
        }

    d = {k: Decimal(v) for k, v in inputs.items()}

    if metric_name == "revenue_growth":
        result = compute_revenue_growth(
            d.get("current"), d.get("prior"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "gross_margin":
        result = compute_gross_margin(
            d.get("revenue"), d.get("gross_profit"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "operating_margin":
        result = compute_operating_margin(
            d.get("revenue"), d.get("operating_income"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "net_margin":
        result = compute_net_margin(
            d.get("revenue"), d.get("net_income"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "net_income_growth":
        result = compute_net_income_growth(
            d.get("current"), d.get("prior"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "current_ratio":
        result = compute_current_ratio(
            d.get("current_assets"),
            d.get("current_liabilities"),
            job_id=job_id,
            period_end=period_end,
        )
    elif metric_name == "asset_liability_ratio":
        result = compute_asset_liability_ratio(
            d.get("total_liabilities"), d.get("total_assets"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "operating_cash_flow_ratio":
        result = compute_operating_cash_flow_ratio(
            d.get("operating_cash_flow"), d.get("revenue"), job_id=job_id, period_end=period_end
        )
    elif metric_name == "free_cash_flow":
        result = compute_free_cash_flow(
            d.get("operating_cash_flow"),
            d.get("capital_expenditure"),
            job_id=job_id,
            period_end=period_end,
        )
    elif metric_name == "roa":
        result = compute_roa(
            d.get("net_income"),
            d.get("total_assets_begin"),
            d.get("total_assets_end"),
            job_id=job_id,
            period_end=period_end,
        )
    else:  # pragma: no cover - 已在白名单前置拦截
        raise ValueError(metric_name)

    return result.model_dump()


# Analysis Agent 最小工具白名单（least-privilege：只有"查事实"和"算指标"两个确定性能力）
_ANALYSIS_TOOLS = [financial_fact_query, financial_calculator]


def build_analysis_agent(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    profile: ResearchProfile | None = None,
) -> Agent:
    """构建财报分析 Agent（统一 LLM 接口）。

    - 只注入 FinancialFactQuery + FinancialCalculator（不给搜索/下载工具）；
    - backstory 使用 analysis_prompt_v2（P06-09A：明确禁止 LLM 算术 + completeness 三态）；
    - 未传 fake 时用 build_real_llm 构造真实 LLM（P05-12B 删除 NotImplementedError）。
    """
    prompt = load_prompt(PromptName.ANALYSIS)
    llm = fake if fake is not None else build_real_llm(config, LLMRole.ANALYSIS)
    resolved = profile if profile is not None else ResearchProfile.for_mode("deep")
    return Agent(
        role="财报分析 Agent",
        goal=(
            "基于上游 ResearchPack 与财务事实，选择可比期间并经由确定性"
            " FinancialCalculator 产出 FinancialAnalysisPack。"
        ),
        backstory=prompt,
        llm=llm,
        tools=_ANALYSIS_TOOLS,
        allow_delegation=False,
        verbose=False,
        max_iter=resolved.analysis_max_iter,
        max_retry_limit=resolved.max_retry_limit,
        max_execution_time=resolved.max_execution_time,
        max_rpm=resolved.max_rpm,
    )


def build_analysis_task(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    agent: Agent | None = None,
    profile: ResearchProfile | None = None,
) -> Task:
    """构建 Analysis Task：用 fake LLM + 分析工具白名单，输出绑定 FinancialAnalysisPack。"""
    task_agent = (
        agent if agent is not None else build_analysis_agent(config, fake=fake, profile=profile)
    )
    return Task(
        description=(
            "分析任务输入：input_company={input_company}，as_of_date={as_of_date}，requested_forms={requested_forms}。\n"
            "以下 financial_facts 是确定性预取并按 as_of_date 截断的 SEC XBRL JSON（可能是 "
            "合法 JSON 数组，也可能是空数组 [] 表示未取到任何事实）：\n"
            "{financial_facts}\n"
            "只允许使用该 JSON 中的 value/unit/period/concept/locator 生成 facts；"
            "禁止从模型知识、新闻摘要或推测填数。\n"
            "如果 financial_facts 是空数组 []：必须输出 schema_version=analysis_pack_v2、"
            "completeness=unavailable、facts=[]、metrics=[]，并在 unavailable_reason 中说明"
            "未取得 SEC 财务事实；禁止输出 {\"ok\": false, ...} 之类的工具错误结构，也不得把"
            "该 JSON 当作文本原样输出。\n"
            "若只有部分可用事实或指标口径缺失：completeness=partial 并在 limitations 说明"
            "缺哪些数据及原因；不得把缺失伪装成 complete。\n"
            "基于上游 ResearchPack 与上述 FinancialFact，选择可比期间与 concept，"
            "调用 FinancialFactQuery 确定口径、FinancialCalculator 完成所有算术，"
            "产出可被 FinancialAnalysisPack(schema v2) 校验通过的结构化对象（只含合法字段）"
            "；completeness=unavailable 是合法业务结果，不是系统异常。"
        ),
        expected_output="一个可被 FinancialAnalysisPack 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=FinancialAnalysisPack,
    )


def build_analysis_pair(
    config: LLMConfig,
    fake: AnyLLM,
    profile: ResearchProfile | None = None,
) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供 P03-08 组合 Crew。"""
    agent = build_analysis_agent(config, fake=fake, profile=profile)
    task = build_analysis_task(config, fake=fake, agent=agent, profile=profile)
    return agent, task
