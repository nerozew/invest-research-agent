# 报表行名 LLM 兜底翻译 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报表行名翻译从"仅通用对照表"升级为"通用对照表 + LLM 兜底"：对照表未命中的行名（任何公司、任何生僻行名）在提取层批量交给 LLM 一次翻译，回填渲染；数字与表格结构始终确定性处理、不经 LLM。

**Architecture:** 新增 `StatementRowTranslator`（`financial/statement_translation.py`）——内部持有可选 LLM completion，`translate_many(labels)` 先查通用对照表（`translate_statement_row`），未命中批量收集后一次 LLM 调用翻译（英文→中文 JSON 映射，代码验证后回填）。渲染器 `render_html_statements` 增加可选 `extra_labels` 参数（翻译时 `extra_labels.get(label) or translate_statement_row(label)`）。`annual_runtime._html_statements_markdown` 在 HTML 原表提取后、渲染前，用注入的 `statement_translator` 补翻译，把 `extra_labels` 传给渲染器。worker 在现有 LLM 注入点（LLMFactExtractor 旁）创建 `StatementRowTranslator` 注入 runtime。

**Tech Stack:** Python 3.12、标准库、Pydantic、pytest、ruff、mypy strict。LLM 调用复用现有 `AnnualLlmDispatcher`/`LLMConfig`（OpenAI-compatible）。

**Spec:** 用户确认的架构方向（对话约定）：
- 不要"针对某家公司"的对照表——对照表是通用 GAAP 行名表（覆盖行业标准行名），未命中的交给 LLM 兜底，所有公司通用。
- 翻译放在**提取/渲染层**（HTML 提取后、渲染前），不放写 agent——写 agent 重写整表会破坏"数字原封不动"。
- 数字、表格结构**绝不经 LLM**；LLM 只翻译行名字符串（英文→中文）。
- 通用对照表已扩充（commit `10c6ac0`：银行业 GAAP 行名 + 括号备注剥离 + 测试）。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`financial/` 与 `reporting/` 层仅标准库 + Pydantic + domain（**不得导入 LLM 具体实现**——LLM 通过注入的 completion 传入，`reporting` 不依赖 infrastructure）；`infrastructure/` 负责注入 LLM。
- 新代码注释用中文。
- 测试用 pytest，新逻辑必须有 failing test 先行。
- 数字/表格内容**不经 LLM 改写**（原表数字原封不动）。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可，不 reformat 无关文件。

---

### Task 1: `StatementRowTranslator` 新模块（对照表 + LLM 批量兜底）

**Files:**
- Create: `src/invest_research/financial/statement_translation.py`
- Test: `tests/test_statement_translation.py`

**Interfaces:**
- Consumes: `translate_statement_row`（`statement_cn_labels.py`，对照表）；注入的 `llm_translate` 可调用对象（协议 `Callable[[tuple[str, ...]], dict[str, str]]`——给定行名列表，返回 `{label_en: label_cn}` 映射；由 **infrastructure 层**构造：用 `AnnualLlmDispatcher` + `LLMRole.WRITER` 调 LLM 并解析 JSON，financial 层不得 import agents/LLM）。
- Produces:
  - `translate_labels_with_llm(labels: tuple[str, ...], llm_translate: Callable[[tuple[str, ...]], dict[str, str]]) -> dict[str, str]`：调 `llm_translate` 拿映射，验证后只保留输入标签子集；任何失败返回 `{}`（best-effort，回退英文）。
  - `class StatementRowTranslator`：`__init__(self, llm_translate: Callable[[tuple[str, ...]], dict[str, str]] | None = None)`；`translate_many(self, labels: tuple[str, ...]) -> dict[str, str]`（对照表优先，未命中走 `translate_labels_with_llm`）；`translate(self, label: str) -> str`（对照表 + 已翻译缓存）。

- [ ] **Step 1: 写失败测试**（mock `llm_translate`）

```python
# tests/test_statement_translation.py
from invest_research.financial.statement_translation import (
    StatementRowTranslator,
    translate_labels_with_llm,
)


def _fake_llm(mapping: dict[str, str]):
    return lambda labels: {k: mapping[k] for k in labels if k in mapping}


def test_translate_labels_with_llm_returns_valid_mapping() -> None:
    llm_translate = _fake_llm({"SomeOddRow": "某生僻行", "AnotherRow": "另一行"})
    result = translate_labels_with_llm(("SomeOddRow", "AnotherRow"), llm_translate)
    assert result == {"SomeOddRow": "某生僻行", "AnotherRow": "另一行"}


def test_translate_labels_with_llm_fails_gracefully() -> None:
    # llm_translate 抛异常 / 返回非 dict / 返回输入外标签 → 全部安全处理
    def bad(labels):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm down")

    assert translate_labels_with_llm(("X",), bad) == {}

    def bad_shape(labels):  # type: ignore[no-untyped-def]
        return "not a dict"

    assert translate_labels_with_llm(("X",), bad_shape) == {}


def test_translator_uses_lookup_table_first_and_llm_for_missing() -> None:
    translator = StatementRowTranslator(_fake_llm({"MysteryRow": "神秘行"}))
    assert translator.translate("Net income") == "净利润"  # 对照表命中
    assert translator.translate("MysteryRow") == "神秘行"  # 对照表未命中 → LLM 兜底
    assert translator.translate_many(("Net income", "MysteryRow")) == {
        "MysteryRow": "神秘行"  # 对照表命中不送 LLM
    }


def test_translator_without_llm_keeps_english() -> None:
    translator = StatementRowTranslator(None)
    assert translator.translate("TotallyUnknownRow") == "TotallyUnknownRow"
```

- [ ] **Step 2: 运行确认失败**（`ModuleNotFoundError`，模块不存在）
- [ ] **Step 3: 实现**

```python
"""src/invest_research/financial/statement_translation.py
报表行名翻译的 LLM 兜底：通用 GAAP 对照表（statement_cn_labels）为主，
未命中的行名批量交给 LLM 一次翻译（英文→中文映射），best-effort。
数字与表格结构绝不经 LLM——本模块只翻译行名字符串。
依赖边界：仅标准库 + financial 层；LLM 通过注入的 ``llm_translate`` 可调用对象
（由 infrastructure 层用 AnnualLlmDispatcher + LLMRole.WRITER 构造）传入。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from invest_research.financial.statement_cn_labels import translate_statement_row

# infrastructure 层注入的翻译可调用：给定行名列表 → {英文行名: 中文行名}。
RowTranslateCallable = Callable[[tuple[str, ...]], dict[str, str]]


def translate_labels_with_llm(
    labels: tuple[str, ...], llm_translate: RowTranslateCallable
) -> dict[str, str]:
    """调注入的 LLM 翻译并验证；任何失败返回 {}（best-effort，回退英文）。

    只保留输入标签子集的合法字符串映射（防 LLM 编造/改数字）。
    """
    unique = tuple(dict.fromkeys(labels))
    if not unique:
        return {}
    try:
        mapping = llm_translate(unique)
    except Exception:  # noqa: BLE001 - 翻译 best-effort，失败回退英文
        return {}
    if not isinstance(mapping, dict):
        return {}
    return {
        key.strip(): str(value).strip()
        for key, value in mapping.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip() and key in unique
    }


class StatementRowTranslator:
    """报表行名翻译：通用 GAAP 对照表为主，LLM 兜底未命中行名。"""

    def __init__(self, llm_translate: RowTranslateCallable | None = None) -> None:
        self._llm_translate = llm_translate
        self._cache: dict[str, str] = {}

    def translate_many(self, labels: tuple[str, ...]) -> dict[str, str]:
        """批量翻译：对照表命中的直接取，未命中的（未在缓存）交给 LLM 一次。"""
        extra: dict[str, str] = {}
        missing: list[str] = []
        for label in dict.fromkeys(labels):
            if label in self._cache:
                extra[label] = self._cache[label]
            else:
                direct = translate_statement_row(label)
                if direct != label:
                    extra[label] = direct
                else:
                    missing.append(label)
        if missing and self._llm_translate is not None:
            translated = translate_labels_with_llm(tuple(missing), self._llm_translate)
            extra.update(translated)
            self._cache.update(translated)
        return extra

    def translate(self, label: str) -> str:
        """单个行名翻译（对照表 → 缓存 → LLM 兜底）。"""
        direct = translate_statement_row(label)
        if direct != label:
            return direct
        if label in self._cache:
            return self._cache[label]
        if self._llm_translate is not None:
            translated = translate_labels_with_llm((label,), self._llm_translate)
            if label in translated:
                self._cache[label] = translated[label]
                return translated[label]
        return label
```

- [ ] **Step 4: 运行确认通过**
Run: `.venv/Scripts/python.exe -m pytest tests/test_statement_translation.py -v`
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/statement_translation.py tests/test_statement_translation.py
git add src/invest_research/financial/statement_translation.py tests/test_statement_translation.py
git commit -m "feat(p07): 报表行名 LLM 兜底翻译（通用对照表未命中时批量翻译）"
```

---

### Task 2: 渲染器接入 `extra_labels`

**Files:**
- Modify: `src/invest_research/reporting/html_statements_renderer.py`（`render_html_statements` 增加 `extra_labels`）
- Test: `tests/test_html_statements_renderer.py`

**Interfaces:**
- Consumes: `translate_statement_row`、`HtmlStatement`。
- Produces: `render_html_statements(statements, *, extra_labels: dict[str, str] | None = None) -> str`——翻译行名时 `extra_labels.get(label) or translate_statement_row(label)`；`extra_labels` 缺省 None 保持现有行为（向后兼容）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_html_statements_renderer.py 追加
def test_render_applies_extra_labels_for_missing_rows() -> None:
    stmt = HtmlStatement(
        kind=FinancialStatementKind.CASH_FLOW,
        rows=(("Net income", "100"), ("MysteryBankRow", "200")),
        source_table_index=0,
    )
    md = render_html_statements((stmt,), extra_labels={"MysteryBankRow": "神秘银行行"})
    assert "神秘银行行" in md
    assert "净利润" in md  # 对照表命中不受影响
    assert "MysteryBankRow" not in md
```

- [ ] **Step 2: 运行确认失败**（当前渲染器无 extra_labels 参数）
- [ ] **Step 3: 实现**：`_render_one` 与 `render_html_statements` 透传 `extra_labels`；行名翻译改 `(extra_labels or {}).get(cell) or translate_statement_row(cell)`。
- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_html_statements_renderer.py` 全量）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/reporting/html_statements_renderer.py tests/test_html_statements_renderer.py
git add src/invest_research/reporting/html_statements_renderer.py tests/test_html_statements_renderer.py
git commit -m "feat(p07): 原表渲染器支持 extra_labels 行名补翻译"
```

---

### Task 3: runtime/worker 注入 + JPM 全链路验证

**Files:**
- Modify: `src/invest_research/infrastructure/annual_runtime.py`（`AnnualRuntimeComponents` 加 `statement_translator`；`_html_statements_markdown` 用其补翻译）
- Modify: `src/invest_research/infrastructure/queue/worker.py`（注入 `StatementRowTranslator`）
- Test: `tests/test_annual_runtime_html_statements.py`

**Interfaces:**
- Consumes: `StatementRowTranslator`（Task 1）、`render_html_statements(statements, extra_labels=...)`（Task 2）。
- Produces: `AnnualRuntimeComponents.statement_translator: StatementRowTranslator | None = None`；`_html_statements_markdown` 在提取原表后：`extra = self._components.statement_translator.translate_many(all_row_labels)`（有则用），`render_html_statements(statements, extra_labels=extra or None)`。worker 在 LLMFactExtractor 注入点（`annual_runtime.py` 组件构造处）同步创建 `StatementRowTranslator(AnnualLlmDispatcher(LLMConfig.from_settings(settings)))` 注入。

- [ ] **Step 1: 写失败测试**（mock translator 注入，断言未命中行名被翻译）

```python
# tests/test_annual_runtime_html_statements.py 追加
def test_html_statements_use_llm_translation_for_missing_rows(tmp_path) -> None:
    # 构造 source.html 含一个对照表未命中的行名；注入 fake statement_translator
    # 返回 {该行名: 中文}；断言渲染输出含中文、不含英文行名。
    ...
```

（复用既有 `test_annual_runtime_html_statements.py` 的 evidence 构造模式；translator 用 fake 类实现 `translate_many`。）

- [ ] **Step 2: 运行确认失败**（当前 `_html_statements_markdown` 不调用 translator）
- [ ] **Step 3: 实现**：runtime 组件加字段 + `_html_statements_markdown` 收集行名、调 translator、传 extra_labels；worker 注入。
- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_annual_runtime_html_statements.py`、`tests/test_annual_runtime.py`）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_runtime.py src/invest_research/infrastructure/queue/worker.py tests/test_annual_runtime_html_statements.py
git commit -m "feat(p07): runtime 接入报表行名 LLM 兜底翻译（worker 注入）"
```

---

### Task 4: 全链路验证（JPM 重跑）

- [ ] **Step 1**: rebuild 镜像 → up api worker → 重跑 JPM research-job。
- [ ] **Step 2**: 检查报告财务报表段：行名全部中文（含之前未翻译的 Investment banking fees 等）、数字原样、表格对齐。
- [ ] **Step 3**: 下载报告到 `real-test-jpm-20260830/` 覆盖。
- [ ] **Step 4**: 全量 `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -3`（无新失败）。

## Self-Review

- **Spec 覆盖**：LLM 兜底翻译模块（Task 1）✓；渲染器 extra_labels（Task 2）✓；runtime/worker 注入（Task 3）✓；JPM 全链路（Task 4）✓。
- **数字安全**：LLM 只接收行名字符串列表（`translate_labels_with_llm` 输入是 label 元组，输出是字符串映射），绝不接触数字/表格——满足"数字原封不动"硬约束。
- **依赖边界**：`financial/statement_translation.py` 只依赖标准库 + `statement_cn_labels`，不 import LLM 具体实现；completion 协议注入。渲染器 `extra_labels` 保持 reporting 层纯函数。
- **向后兼容**：渲染器 `extra_labels` 带默认 None；runtime `statement_translator` 带默认 None（未注入时回退纯对照表）。
