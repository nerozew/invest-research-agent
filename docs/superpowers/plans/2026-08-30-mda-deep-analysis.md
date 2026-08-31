# MD&A 深度分析优化 + 真实评测 + GitHub PR 计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把报告"管理层讨论与分析"（MD&A）从 300-500 字简略摘译升级为"资深分析师深度要点梳理"（1000-2000 字，多维度信息提取），花更多 token/时间换取信息密度。优化后用 3 家公司真实评测记录 token 数与耗时，最后提交 GitHub 并发 PR。

**Architecture:** 优化 `annual_llm_writing.summarize_mda`：① 输入原文截取 `_MDA_SUMMARIZE_MAX_CHARS` 4000 → 12000 字符（给 LLM 更多原文）；② 输出下限 `_MDA_SUMMARY_MIN_CHARS` 100 → 400；③ system_prompt 改为"资深财报分析师深度要点梳理"，按 7 个维度组织（经营业绩/分部表现/成本费用/资本配置流动性/资产负债现金流/风险不确定性/前瞻展望），要求引用原文数字措辞；④ `max_tokens` 2000 → 6000。Analysis 角色 thinking 已关闭（此前修复），给足 token 空间。

**Tech Stack:** Python 3.12、pytest、ruff、mypy strict、docker compose（评测）。

**Spec:** 用户需求（对话约定）：
- MD&A 要像"管理层分析信息提取的大师"，多写、信息密度高，可多花 token/时间。
- 优化后做 3 家左右公司真实评测，记录 token 数与耗时，真正测试系统。
- 提交 GitHub 并发 PR（用户问过 PR 含义，已解释）。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- LLM 输出**只基于 MD&A 原文**：不得编造、不得补充外部信息、不得给投资建议（沿用现有约束）。
- 新代码注释用中文。
- 测试用 pytest，failing test 先行。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可。

---

### Task 1: MD&A 深度化

**Files:**
- Modify: `src/invest_research/infrastructure/annual_llm_writing.py`（`summarize_mda` + 常量）
- Modify: `tests/test_annual_mda_translation.py`（或相关测试）

**Interfaces:**
- Consumes: `extract_mda`、`_MDA_SUMMARIZE_MAX_CHARS`、`_MDA_SUMMARY_MIN_CHARS`。
- Produces: `summarize_mda` 返回 1000-2000 字深度要点（多维组织）；过短回退原文直取（现有语义保留）。

- [ ] **Step 1: 写失败测试**（断言 prompt/max_tokens 变化；用 fake completion 断言 system_prompt 含"多维/深度"要求、max_tokens=6000）

```python
# tests/test_annual_mda_translation.py 追加
def test_summarize_mda_uses_deep_analysis_prompt_and_big_budget() -> None:
    from types import SimpleNamespace

    class _Fake:
        def __init__(self): self.calls = []
        def complete(self, *, role, system_prompt, user_prompt, max_tokens=500):
            self.calls.append((system_prompt, max_tokens))
            return SimpleNamespace(markdown="经营业绩：公司营收增长…（1000+ 字深度分析）")

    from invest_research.infrastructure.annual_llm_writing import AnnualLlmDispatcher
    # 直接测 summarize_mda 需要一个可注入 completion 的对象；
    # 若 summarize_mda 在 AnnualSectionExecutor 上，构造一个带 fake completion 的 executor。
    ...
    assert fake.calls[0][0] 包含 "资深" and "分部" and "前瞻"
    assert fake.calls[0][1] >= 6000
```

（具体构造以现有测试模式为准；核心断言：prompt 深度化 + max_tokens 提至 6000。）

- [ ] **Step 2: 运行确认失败**（当前 max_tokens=2000、prompt 简略）
- [ ] **Step 3: 实现**

```python
_MDA_SUMMARIZE_MAX_CHARS = 12_000   # 4000 → 12000：给 LLM 更多 MD&A 原文
_MDA_SUMMARY_MIN_CHARS = 400        # 100 → 400：深度分析的下限，过短回退

def summarize_mda(self, blocks):
    mda = extract_mda(blocks)
    if mda is None:
        return ""
    source_text = mda.text[:_MDA_SUMMARIZE_MAX_CHARS]
    result = self._completion.complete(
        role=LLMRole.ANALYSIS,
        system_prompt=(
            "你是资深财报分析师，擅长从上市公司 10-K 的管理层讨论与分析（MD&A）"
            "中提取关键信息。请对给定的 MD&A 原文做深度要点梳理（中文，1000-2000 字），"
            "按以下维度组织：\n"
            "① 经营业绩与收入/利润驱动\n"
            "② 分部或产品线表现\n"
            "③ 成本与费用结构变化\n"
            "④ 资本配置、分红回购与流动性\n"
            "⑤ 资产负债与现金流\n"
            "⑥ 管理层强调的风险与不确定性\n"
            "⑦ 前瞻性展望\n"
            "引用原文中的具体数字与措辞支撑每个要点。只使用原文出现的事实，不得编造、"
            "不得补充外部信息、不得给出投资建议。直接输出内容，不要开场白或元叙述。"
        ),
        user_prompt=f"# 管理层讨论与分析原文节选\n{source_text}",
        max_tokens=6_000,
    )
    summary = (result.markdown or "").strip()
    if len(summary) < _MDA_SUMMARY_MIN_CHARS:
        return ""
    return summary
```

- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_annual_mda_translation.py`、`tests/test_annual_runtime.py`）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_llm_writing.py tests/test_annual_mda_translation.py
git add src/invest_research/infrastructure/annual_llm_writing.py tests/test_annual_mda_translation.py
git commit -m "feat(p07): MD&A 深度化——资深分析师多维要点梳理（1000-2000 字）"
```

---

### Task 2: 3 家公司真实评测（记录 token 与耗时）

- [ ] **Step 1**: rebuild 镜像 → up api worker。
- [ ] **Step 2**: 选 3 家跨行业公司（如 GOOGL / JPM / NKE，或 PFE / XOM）各提交 research-job。
- [ ] **Step 3**: 每份报告记录：任务耗时（提交→完成）、LLM token 数（从 job 的 run_manifest / performance 读）、MD&A 章节字数。
- [ ] **Step 4**: 汇总评测表（公司 / 行业 / 耗时 / token / MD&A 字数 / 行名翻译覆盖率），确认 MD&A 深度化后更充实。

---

### Task 3: GitHub 提交 + PR

- [ ] **Step 1**: 确认 git remote（`git remote -v`）；若无远端，提示用户配置。
- [ ] **Step 2**: 汇总当前分支全部改动（本轮 + 之前的报表/翻译/resolver 计划），确认提交齐全。
- [ ] **Step 3**: `git push` 当前分支到远端。
- [ ] **Step 4**: 用 GitHub CLI（`gh pr create`）创建 PR（base = 目标分支，如 main/develop；title/body 中文描述改动）。
- [ ] **Step 5**: 给用户 PR 链接。

## Self-Review

- **Spec 覆盖**：MD&A 深度化（Task 1）✓；3 家评测记录 token/时间（Task 2）✓；GitHub 提交 + PR（Task 3）✓。
- **信息密度**：多维度 + 引用数字措辞 + 1000-2000 字，比简略摘译显著充实。
- **不编造**：prompt 明确"只使用原文事实，不得编造/补充/投资建议"（沿用现有约束）。
- **评测可复现**：token 从 run_manifest/performance 读，耗时从 job 生命周期算。
