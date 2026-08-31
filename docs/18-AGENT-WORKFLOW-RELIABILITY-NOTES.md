# Agent 工作流可靠性：问题复盘、解决方法与项目升级建议

> 用途：学习笔记、项目复盘、面试讲解和后续可靠性改造依据。
>
> 适用场景：多 Agent、多工具、结构化输出、长工作流、可恢复执行。

## 1. 问题背景

复杂 Agent 系统通常同时包含：

- 多个职责不同的 Agent；
- 多种输入参数不同的工具；
- 上游 Agent 向下游 Agent 传递中间工件；
- LLM 的概率性输出；
- 外部 API 超时、限流和数据缺失；
- 报告发布前的结构、事实、引用和质量检查。

传统函数在输入确定时通常能返回确定类型的输出；LLM 则可能产生字段缺失、类型错误、自由文本、错误工具参数，甚至把 `Action Input` 当作最终答案。因此，生产级 Agent 系统的目标不是“保证模型永不犯错”，而是：

1. 尽早发现错误；
2. 把错误限制在当前阶段；
3. 只重做必要部分；
4. 保存已验证的中间结果；
5. 对可恢复错误有限重试；
6. 对永久错误安全失败或拒绝发布；
7. 保留可审计的错误与证据。

## 2. 严格 Schema 为什么仍然会失败

Schema 只能保证“数据形状”，不能自动保证数据存在、真实或符合业务语义。

常见失败原因：

1. **Schema 与真实业务边界冲突**：例如没有取得 SEC 数据时，Schema 却要求 `facts` 至少一条，模型只能编造、反复调用工具或耗尽迭代次数。
2. **工具契约与 Pack 契约混淆**：工具参数合法不代表 Agent 最终输出的 `ResearchPack` 合法。
3. **框架协议未完成**：Agent 在 ReAct 循环中输出了 `Action/Action Input`，但达到 `max_iter` 前没有输出 Final Answer。
4. **上下文交接不稳定**：下游收到的可能是 Pydantic 对象、字典、原始 JSON、普通文本或工具过程记录。
5. **提示词、工具白名单和领域模型发生漂移**：提示词要求的工具或字段与代码实际能力不一致。
6. **内部校验过早抛错**：框架在 `kickoff()` 内部校验失败并终止，外部 Flow 没有机会执行精确修复或保存上游结果。

正确结论不是“放松所有 Schema”，而是：

> 核心字段保持严格；对真实存在的数据缺失使用显式状态；把结构校验、业务校验、跨 Pack 校验和最终质量门禁分层执行。

## 3. 用状态表达业务完整程度

建议对需要表达数据完整程度的 Pack 增加业务状态，例如：

| 状态 | 中文语义 | 数据要求 | 下游行为 |
|---|---|---|---|
| `complete` | 完整完成 | 核心 facts 必须非空 | 正常分析和发布 |
| `partial` | 部分完成 | 可以缺少部分数据，但 `limitations` 必须说明影响 | 允许部分报告、醒目标注限制 |
| `unavailable` | 数据不可获得 | facts/metrics 为空，`unavailable_reason` 必填 | 跳过计算，拒绝或发布数据不可用说明 |

这类状态是 **Pack 的业务完整性状态**，不同于 Job 的运行状态：

- Job：`pending/running/succeeded/failed/cancelled`；
- Pack：`complete/partial/unavailable`。

因此可以出现：

```text
Job=succeeded + AnalysisPack=partial
```

它表示系统正常完成，没有程序故障，但只取得了部分业务数据。

状态不能只是一个自由字段，必须通过跨字段校验建立不变量：

```text
complete    -> facts 非空
partial     -> limitations 非空
unavailable -> facts/metrics 为空且 unavailable_reason 非空
```

## 4. 五层门禁模型

### 4.1 工具参数门禁

检查工具名称、必填参数、类型、日期格式和枚举。工具参数错误只修正本次调用，不重跑整个 Job。

### 4.2 Pack 结构门禁

从 Agent 原始输出中提取候选 JSON，拒绝 `Action Input`、自由文本和错误对象，再用 Pydantic 校验 Pack 字段与类型。

### 4.3 业务不变量门禁

例如：

- `as_of_date` 不得漂移；
- `period_end <= as_of_date`；
- SEC 来源必须有 URL 与 locator；
- 指标必须由确定性计算器生成；
- 空 facts 必须对应 `partial/unavailable` 和原因；
- 单位、期间和 concept 必须可比。

### 4.4 跨 Pack 一致性门禁

例如：

- Analysis 与 Research 必须是同一公司和截止日；
- Writer 使用的数字必须存在于 AnalysisPack；
- citation key 必须存在于上游来源或指标；
- Writer 不得引入两个上游 Pack 之外的新事实。

### 4.5 最终质量门禁

检查章节、引用、数字一致性、限制披露、禁止投资建议和免责声明。质量门禁负责发布决策，但不能替代前面的结构与业务校验。

## 5. Checkpoint 的正确含义

Checkpoint 不是简单的“暂停”，而是一个已经通过当前阶段校验、可恢复、可复用、带版本和校验和的中间工件。

推荐流程：

```text
Research Agent
  -> ResearchPack 结构/业务校验
  -> 保存 Research Checkpoint
  -> Analysis Agent
  -> AnalysisPack 结构/业务/跨 Pack 校验
  -> 保存 Analysis Checkpoint
  -> Writer Agent
  -> ReportDraft 结构/引用校验
  -> 保存 Writer Checkpoint
  -> 最终质量门禁
  -> 发布或拒绝
```

Checkpoint 至少记录：

- `job_id`、`step_name`；
- Pack JSON；
- `schema_version`；
- `input_hash`、上游 checksum；
- prompt/model/formula 版本；
- 完成时间、尝试次数；
- 校验结果与错误分类。

有了 Checkpoint，Analysis 失败时不需要重新搜索；Writer 失败时不需要重新搜索和计算。

## 6. 错误分类与最小补偿

| 错误类型 | 示例 | 补偿方法 | 是否重跑工具 |
|---|---|---|---|
| 工具输入错误 | 参数名/日期格式错误 | 只修正该次工具参数 | 仅该工具 |
| 网络暂时错误 | timeout/429/5xx | 按错误白名单和 Retry-After 重试 | 是，有限次 |
| Pack 结构错误 | 缺字段、错误 JSON | 把 Pydantic 字段错误反馈给结构修复器 | 否 |
| 合法业务缺失 | 没有可用 SEC facts | `partial/unavailable` + 原因 | 视情况补证 |
| 跨 Pack 不一致 | Writer 数字不在 AnalysisPack | 退回责任阶段定向修复 | 否或局部 |
| 报告表达问题 | 缺少章节/免责声明 | 只修订 Writer | 否 |
| 永久错误 | 无效密钥、身份歧义、Schema 永久不兼容 | failed/rejected/人工确认 | 否 |

重试预算必须分开，不能全部塞进 `max_iter`：

```text
agent_tool_iterations       # Agent 思考/工具循环
schema_repair_attempts      # 只修复结构，建议 1 次
network_retry_attempts      # 网络重试，按错误类型
business_revision_attempts  # Writer 定向修订，建议 1 次
supplement_attempts         # Research 补证，建议 1 次
```

结构修复阶段应禁止重新调用 SEC、搜索和下载工具，避免只缺一个字段却重新执行整套研究。

## 7. 鸭子类型、Protocol 和 Pack 的边界

鸭子类型/Protocol 适合“能力实现”：只要对象具备 `name + execute()`，真实工具和 Fake 工具就可以互换。

鸭子类型不适合跨 Agent 的业务 Pack。两个对象即使都有 `sources` 字段，其内部结构和语义也可能完全不同。跨 Agent 数据应该：

- 使用显式 Pydantic 模型；
- 带 schema version；
- 经过 Adapter 规范化；
- 校验成功后才交给下游。

可以对外把三 Agent 封装成一个 `workflow.run()`，但内部必须保留阶段边界、门禁和 Checkpoint，不能变成一次不可恢复的黑盒调用。

## 8. 当前项目已经实现的能力

### 已实现

- `ResearchPack/FinancialAnalysisPack/ReportDraft/QualityReport` Pydantic 模型；
- Job/Step 状态机、实时 `current_step` 和步骤终态收口；
- 工具白名单与每 Job `ToolBudget`；
- SEC/Serper 重试、限流、缓存和并行预取；
- `as_of_date`、分析期间、章节、禁用投资建议等确定性质量分类；
- 结构化 `QualityIssue + severity + action`；
- Writer 修订最多一次、Research 补证最多一次；
- 失败任务进入 failed，并记录 error code/message/failure stage；
- input hash/schema version 的重算决策纯函数；
- 最终中间工件、manifest、Markdown/PDF 发布；
- Prometheus、Grafana、OpenTelemetry/Jaeger 可观测性。

### 部分实现

- Research 输出由 runner 外部解析和一次确定性收尾；Analysis/Writer 仍依赖 CrewAI 内部 `output_pydantic`，三阶段策略不统一；
- `guardrails.py` 已有结构校验和有限修复纯函数，但没有接入 live 主流程；
- workflow step 已实时保存状态，但 Pack 内容和工件不是阶段完成后立即持久化；
- versioning 服务已存在，但尚未形成“从 Checkpoint 恢复并跳过已验证阶段”的完整执行器；
- 当前工作区正在修复 Writer 的 ArtifactReader，使它真实读取上游 Task 输出；同时新增统一 Pack 解析、失败分类和 `failure_stage`，但这些修改尚未提交；
- 新增的 Pack 解析目前只负责“解析/拒绝/分类”，尚未形成“精确字段反馈 + 最多一次纯结构修复 + 再校验”的完整 `PackBoundary`；
- 新增的失败分类目前负责记录稳定错误码，不等于对应错误已经真正接入自动重试或定向补偿路由。

### 当前缺失或存在漂移

- `crew.kickoff()` 仍一次运行三个 Agent；中间 Agent 失败时，无法从已验证的阶段 Checkpoint 恢复；
- Pack 在三个 Agent、质量门禁和 manifest 全部完成后才统一落盘，不是真正的阶段级 Checkpoint；
- Analysis 提示词声明有 ArtifactReader，但实际工具白名单只有 FinancialFactQuery/FinancialCalculator；
- Analysis 提示词仍写 `facts` 至少一条，而领域模型已经允许空 facts；
- 缺少统一的 `PackBoundary`：结构提取、Pydantic 校验、业务不变量、错误分类、有限结构修复；
- `FinancialAnalysisPack` 尚未显式表达 `complete/partial/unavailable`；
- 缺少针对提示词工具列表、实际工具白名单和 Pack Schema 的自动一致性测试。

## 9. 必要升级与优先级

### P0：先完成现有交接修复

先验证并提交当前 Writer ArtifactReader 的真实上游读取改动；同时修复提示词与工具白名单/领域模型漂移。不要在脏工作区同时进行数据库和 Flow 大改。

当前路线图的 `P06-09` 已明确用于 GitHub Actions CI，而未提交代码注释也使用了“P06-09”描述 Pack 解析/失败分类，存在任务编号冲突。收口时应把可靠性补充改成独立补充编号（例如 `P06-08A` 或 `P06-R1`），不要覆盖原 CI 任务语义。

### P1：增加统一 PackBoundary（必要）

三个 Agent 统一经过外部边界：提取候选输出、拒绝 Action Input、Pydantic 校验、业务校验、错误分类、最多一次纯结构修复。

### P2：为 AnalysisPack 增加完整性状态（建议）

增加 `complete/partial/unavailable` 和跨字段校验；同步 migration（如果数据库直接存字段）、提示词、质量门禁、报告模板、API/前端和测试。若状态只保存在 Pack JSON 中，可以先不改数据库列。

### P3：阶段级 Checkpoint（必要但改动较大）

把一次三 Agent kickoff 改成 Flow 分阶段调用和保存。每个阶段只有在结构、业务和 checksum 校验成功后才保存 Checkpoint；重启后根据 input hash/schema version 决定复用或重算。

### P4：评测和人工边界（后续）

使用固定公司/日期/fixture 做模型与提示词回归；对低证据、身份歧义和高风险结论进入人工确认，不让模型自行补齐不确定事实。

## 10. 面试表达模板

> 项目早期采用 CrewAI sequential 一次执行三个 Agent，并通过 Pydantic 约束结构化输出。真实联调后发现，严格 Schema 不能自动解决外部数据缺失、框架 Action Input 被误识别、提示词与工具能力漂移等问题；一旦中间阶段校验失败，整条 kickoff 会终止并重复消耗模型和工具成本。
>
> 我把可靠性设计升级为分层门禁和最小补偿：工具输入、Pack 结构、业务不变量、跨 Pack 一致性、最终质量五层校验；将网络重试、Schema 修复、业务修订和补证预算分离；使用 complete/partial/unavailable 显式表达业务数据完整程度；规划阶段级 Checkpoint，使 Research 成功后立即持久化，后续失败只重跑责任阶段。最终目标不是让 Agent 永不犯错，而是让错误尽早暴露、可分类、可恢复、可审计，并在无法恢复时安全失败。

## 11. 简历亮点表达

- 设计多 Agent 分层契约与质量门禁，覆盖结构校验、业务不变量、跨阶段一致性和引用追踪；
- 将网络重试、工具预算、Schema 修复、业务反思分离为独立有界策略，防止无效重试与 Token 膨胀；
- 引入显式业务完整性状态和阶段级 Checkpoint 方案，实现部分成功、失败收口和最小阶段重算；
- 基于 Pydantic、条件状态更新、checksum/input hash 和版本化工件构建可审计、可恢复的 Agent 工作流。
