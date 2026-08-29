"""三张财务报表的确定性导出（资产负债表 / 利润表 / 现金流量表）。

数据来源是 SEC Company Facts 全量 ``FinancialFact``：三张表的每个行项目
本来就是带标签的结构化数字，本模块按行项目映射（``statements_v1.json``）
筛出 target（可选 comparator）财年的值，组装为 ``FinancialStatementSet``。

保真边界：
- 数字直取 ``FinancialFact.value``，**不经任何 LLM**；
- 复用 ``annual_comparison`` 的严格筛选模式（财年 + FY 期间 + 10-K 表单 +
  accession + instant/duration），单概念多值冲突拒绝，缺失行降级为
  ``value=None`` 并记 limitation，不阻塞整表。

依赖边界：仅标准库、Pydantic、domain、financial 层；禁止导入 CrewAI/SQLAlchemy。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.models import FinancialFact
from invest_research.financial.annual_comparison import _select_one

STATEMENTS_V1_PATH = Path(__file__).parent / "mappings" / "statements_v1.json"


class StatementPeriodType(StrEnum):
    """报表行项目的期间类型：时点（资产负债表）或期间（利润/现金流）。"""

    INSTANT = "instant"
    DURATION = "duration"


class FinancialStatementKind(StrEnum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"


class StatementRowDef(BaseModel):
    """单个行项目的映射定义（label + concept 候选优先级）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    candidates: tuple[str, ...] = Field(min_length=1)


class StatementDef(BaseModel):
    """一张表的映射定义（表名 + 期间类型 + 行项目）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    period_type: StatementPeriodType
    rows: tuple[StatementRowDef, ...] = Field(min_length=1)


class StatementMapping(BaseModel):
    """版本化三张表行项目映射集合。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    statements: dict[str, StatementDef] = Field(default_factory=dict)


class BalanceCheckResult(BaseModel):
    """资产负债表勾稽校验结果（可算才校验）。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    message: str | None = None


class FinancialStatementRow(BaseModel):
    """报表单行：label + 命中 concept + target/comparator 值（数字原封不动）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    concept: str | None = None
    unit: str | None = None
    period_type: StatementPeriodType
    period_end: date | None = None
    accession_number: str | None = None
    value: Decimal | None = None
    comparator_value: Decimal | None = None


class FinancialStatementSet(BaseModel):
    """一张完整报表的确定性导出结果。"""

    model_config = ConfigDict(frozen=True)

    kind: FinancialStatementKind
    label: str = Field(min_length=1)
    target_year: int = Field(ge=1900, le=9999)
    comparator_year: int | None = None
    rows: tuple[FinancialStatementRow, ...] = ()
    limitations: tuple[str, ...] = ()
    balance_check: BalanceCheckResult | None = None


def load_statement_mapping(path: Path = STATEMENTS_V1_PATH) -> StatementMapping:
    """从 JSON 数据文件加载并校验三张表映射（fail-fast）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return StatementMapping.model_validate(raw)


def extract_statements(
    facts: list[FinancialFact],
    mapping: StatementMapping,
    *,
    target_year: int,
    target_accession: str,
    target_report_date: date | None = None,
    comparator_year: int | None = None,
    comparator_accession: str | None = None,
    comparator_report_date: date | None = None,
) -> tuple[FinancialStatementSet, ...]:
    """确定性导出三张表：按行项目映射筛出 target（可选 comparator）财年值。

    缺失行 → ``value=None`` + limitation（不阻塞）；单概念多值冲突 → 丢弃该行。
    """
    sets: list[FinancialStatementSet] = []
    all_limitations: list[str] = []
    for kind in FinancialStatementKind:
        definition = mapping.statements.get(kind.value)
        if definition is None:
            continue
        instant = definition.period_type is StatementPeriodType.INSTANT
        rows: list[FinancialStatementRow] = []
        for row_def in definition.rows:
            target = _select_one(
                facts,
                candidates=row_def.candidates,
                fiscal_year=target_year,
                accession=target_accession,
                report_date=target_report_date,
                instant=instant,
                logical_name=row_def.label,
            )
            comparator = None
            if comparator_accession is not None and comparator_year is not None:
                comparator = _select_one(
                    facts,
                    candidates=row_def.candidates,
                    fiscal_year=comparator_year,
                    accession=comparator_accession,
                    report_date=comparator_report_date,
                    instant=instant,
                    logical_name=row_def.label,
                )
            if target.limitation:
                all_limitations.append(f"{kind.value}.{row_def.label}: {target.limitation}")
            if comparator is not None and comparator.limitation:
                all_limitations.append(f"{kind.value}.{row_def.label}: {comparator.limitation}")
            fact = target.fact if target.fact is not None else (
                comparator.fact if comparator is not None else None
            )
            rows.append(
                FinancialStatementRow(
                    label=row_def.label,
                    concept=fact.concept if fact is not None else None,
                    unit=fact.unit if fact is not None else None,
                    period_type=definition.period_type,
                    period_end=(
                        fact.period_end if fact is not None and fact.period_end is not None
                        else (fact.instant_date if fact is not None else None)
                    ),
                    accession_number=fact.accession_number if fact is not None else None,
                    value=target.fact.value if target.fact is not None else None,
                    comparator_value=(
                        comparator.fact.value
                        if comparator is not None and comparator.fact is not None
                        else None
                    ),
                )
            )
        sets.append(
            FinancialStatementSet(
                kind=kind,
                label=definition.label,
                target_year=target_year,
                comparator_year=comparator_year,
                rows=tuple(rows),
                limitations=tuple(dict.fromkeys(all_limitations)),
                balance_check=(
                    _balance_check(rows) if kind is FinancialStatementKind.BALANCE_SHEET else None
                ),
            )
        )
    return tuple(sets)


def _balance_check(rows: list[FinancialStatementRow]) -> BalanceCheckResult | None:
    """资产 == 负债 + 权益 勾稽校验（任一缺失则返回不可校验）。"""
    by_label: dict[str, Decimal] = {
        row.label: row.value for row in rows if row.value is not None
    }
    assets = by_label.get("资产总计")
    liabilities = by_label.get("负债合计")
    equity = by_label.get("股东权益")
    if assets is None or liabilities is None or equity is None:
        return BalanceCheckResult(ok=False, message="缺少资产/负债/权益任一项，无法校验勾稽关系")
    difference = assets - (liabilities + equity)
    if difference == 0:
        return BalanceCheckResult(ok=True, message="资产 = 负债 + 权益 成立")
    return BalanceCheckResult(ok=False, message=f"资产 - (负债 + 权益) = {difference}")


__all__ = [
    "BalanceCheckResult",
    "FinancialStatementKind",
    "FinancialStatementRow",
    "FinancialStatementSet",
    "StatementDef",
    "StatementMapping",
    "StatementPeriodType",
    "StatementRowDef",
    "STATEMENTS_V1_PATH",
    "extract_statements",
    "load_statement_mapping",
]
