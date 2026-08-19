"""P06-11F：确定性有界修订器（DeterministicRevision）。

设计动机：
- 当报告只因可修复质量问题被拒绝时（missing_citation_keys、
  invalid_citation_key、forbidden_advice、missing_section），允许有界修订一次；
- 修订**只**使用：原始 Markdown、Quality Gate issue codes、CitationRegistry、
  必需章节；不重新执行 Research/Analysis、不重新调用 SEC/Serper/
  FinancialCalculator，**不增加事实**；
- 修订是纯函数（不调用 LLM、不发起网络），因此天然满足
  "修订不重新调用 Research/Analysis/外部工具"。

修订规则（确定性、可测试）：
1. ``missing_citation_keys``：若正文中已出现注册表中的合法 key 但未加括号，
   把它们包装成固定格式 ``[src_...]``/``[fr_...]``；
   若正文完全没有 key（注册表为空）→ 返回 None（不可修复，保持 rejected）。
2. ``forbidden_advice``：删除整行包含真实投资建议的句子（删除建议不增加事实；
   免责声明中的关键词（"不构成买入…"）不删除）。
3. ``invalid_citation_key``：删除正文中仅由 `[...]` 包裹但不在注册表中的
   key（模型自行生成的伪造 key）。若删除后正文仍有非法 key → 不可修复。
4. ``missing_section``：**不自动补造章节标题**（禁止为必填字段补造内容）；
   本修订器不修复章节缺失（保持 rejected，由人工/后续任务处理）。

调用方（Flow 层）负责：
- 最多修订一次；
- 修订后重新执行 ReportDraftAssembler 与 Quality Gate；
- 第二次仍失败保持 rejected，并保留原稿、修订稿与质量报告；
- 记录 revision_attempted/revision_succeeded 指标与 span。

依赖方向：application → 标准库 + CitationRegistry。纯函数，不导入 CrewAI。
"""

from __future__ import annotations

import re
from typing import Any

# 可修复的 issue code 白名单（与 Quality Gate 的稳定 code 一一对应）。
REVISABLE_ISSUE_CODES: frozenset[str] = frozenset(
    {
        "missing_citation_keys",
        "invalid_citation_key",
        "forbidden_advice",
    }
)

# 固定引用格式：`[key]`
_CITATION_RE = re.compile(r"\[([A-Za-z0-9_]{4,64})\]")

# 真实投资建议句子（与 quality_classifier 的拒绝模式保持同源语义）。
# 仅用于删除“肯定建议”，免责声明中的关键词不在删除范围。
_ADVICE_SENTENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"建议\s*(买入|卖出|增持|减持|清仓|持有)"),
    re.compile(r"(应当|应该|应)\s*持仓"),
    re.compile(r"目标价(格)?\s*(为|是|：|:)?\s*[$￥]?\s*\d[\d,]*(\.\d+)?"),
    re.compile(r"(将|会|必然|保证)\s*上涨\s*\d"),
    re.compile(r"确定性收益|保证收益|承诺收益|稳赚"),
    re.compile(r"(买入|卖出|增持|减持|持有)\s*评级"),
)

_DISCLAIMER_NEGATIONS: tuple[str, ...] = (
    "不构成",
    "不提供",
    "不建议",
    "并非",
    "不表示",
    "不代表",
    "非投资建议",
    "不应当",
    "不应被解读",
)
_NEGATION_WINDOW = 40


def _is_disclaimer_context(text: str, key_start: int) -> bool:
    """判断命中位置是否处于免责声明否定语境（与 Quality Gate 同源）。"""
    before = text[max(0, key_start - _NEGATION_WINDOW) : key_start]
    return any(neg in before for neg in _DISCLAIMER_NEGATIONS)


def _line_contains_advice(line: str) -> bool:
    """判断一行是否包含真实投资建议（不含免责声明语境）。"""
    for pattern in _ADVICE_SENTENCE_PATTERNS:
        for match in pattern.finditer(line):
            if not _is_disclaimer_context(line, match.start()):
                return True
    return False


def _wrap_citation_keys(markdown: str, valid_keys: set[str]) -> str:
    """把正文中已出现的合法 key 包装为固定 ``[key]`` 格式（不增加事实）。

    只处理完整单词边界上的 key（避免误伤 `src_abc` 之类子串）。
    """
    for key in sorted(valid_keys, key=len, reverse=True):
        # 已加括号的 key 跳过
        pattern = re.compile(rf"(?<!\[)\b{re.escape(key)}\b(?!\])")
        markdown = pattern.sub(f"[{key}]", markdown)
    return markdown


def _strip_invalid_citation_keys(markdown: str, valid_keys: set[str]) -> tuple[str, bool]:
    """删除正文中 `[...]` 包裹但不在注册表中的 key（模型伪造）。

    返回 (修订后文本, True)。删除非法 key 本身就是清理成功；
    无法识别为 citation key 的括号文字（如网页链接文本）保持不动。
    """
    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner in valid_keys:
            return match.group(0)
        if inner.startswith(("src_", "fr_", "claim_")):
            # 模型自行生成的伪造 key：删除（不增加事实，只清理）。
            return ""
        # 非 citation key 格式的括号文字（如网页链接文本）不删除
        return match.group(0)

    revised = _CITATION_RE.sub(_replace, markdown)
    return revised, True


def deterministic_revise(
    markdown: str,
    issue_codes: list[str],
    registry: Any | None,
    required_sections: list[str] | None = None,
) -> str | None:
    """确定性修订一次报告正文。

    返回修订后的 Markdown；无法修复（注册表为空且无 key 可包装、
    存在无法清理的非法 key）时返回 None——调用方保持 rejected。

    - ``issue_codes``：Quality Gate issue codes（可修复白名单内的才会处理）；
    - ``registry``：CitationRegistry（提供合法 key 集合；可为 None）；
    - ``required_sections``：仅用于记录（本修订不自动补造章节）。
    """
    text = str(markdown or "")
    valid_keys: set[str] = set(registry.keys()) if registry is not None else set()
    revised = text

    changed = False
    for code in issue_codes:
        if code not in REVISABLE_ISSUE_CODES:
            continue
        if code == "missing_citation_keys":
            if not valid_keys:
                # 注册表为空且正文无 key → 无法修复（不伪造引用）
                if _CITATION_RE.search(revised) is None:
                    return None
                continue  # 有 key 可包装，继续
            wrapped = _wrap_citation_keys(revised, valid_keys)
            if wrapped != revised:
                revised = wrapped
                changed = True
        elif code == "forbidden_advice":
            lines = revised.splitlines()
            kept: list[str] = []
            removed = False
            for line in lines:
                if _line_contains_advice(line):
                    removed = True
                    continue
                kept.append(line)
            if removed:
                revised = "\n".join(kept)
                changed = True
        elif code == "invalid_citation_key":
            cleaned, ok = _strip_invalid_citation_keys(revised, valid_keys)
            if cleaned != revised:
                revised = cleaned
                changed = True
            if not ok:
                return None

    if not changed:
        return None
    return revised
