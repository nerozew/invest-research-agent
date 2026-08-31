# P07：年度双期间并行投研流水线（ADR + 业务定义）

> 状态：已批准的设计基线；仅定义业务与架构，不改变当前运行路径。
>
> 范围：以同一 SEC 公司 FY Y 与 FY Y-1 的年度研究为起点，先实现 `annual_deep`。

## 1. 要解决的问题

当前流程在公司解析后的 SEC/搜索预取阶段有并发，但 Research、Analysis、Writer
仍按完整阶段串行执行。每个 Job 也只有固定 00--07 步骤，不能表达单份 filing
下载、解析、验证、重试以及在证据到达时解锁下游工作。

P07 的目标是缩短关键路径，同时不牺牲财务可追溯性：独立 I/O 与解析可并发，只有
真正存在数据依赖的分析和最终汇总才等待。

## 2. 业务范围

### 2.1 `annual_deep` 模式

- 输入：公司、`as_of_date`、报告语言；目标财年由系统自动选择为该截止日前可用的
  最新 10-K 的 SEC `reportDate` 所属财年，而不是使用 filing 日期的日历年；
- 对象：同一家公司 FY Y 与 FY Y-1，不是两家公司，也不按 filing 日历年替代财年；
- 财务比较必需证据：FY Y 10-K，以及可验证 FY Y 与 FY Y-1 的 SEC Company Facts；
- FY Y-1 10-K 是风险、MD&A 和其他叙事同比的优先证据，而不是基础财务同比的
  无条件前置条件；
- 按需证据：仅在明确 Evidence Gap 下补充 10-Q、8-K 或可信背景资料；
- 非目标：实时/日度/月度行情、自动交易、个性化投资建议、无限自主搜索。

`as_of_date` 是不可突破的边界。若目标财年的 10-K 在该日期前尚未公开，系统只可
交付已验证的非财务内容及明确限制说明，禁止财务同比、禁止使用未来 filing，也绝不
以 10-Q 静默替代 10-K。若上一年度 10-K 缺失但两年 Company Facts 已验证，系统可
输出财务同比，但必须禁止风险、MD&A 与其他叙事同比，并披露该限制。

年度 selector 仅接受带 `reportDate` 的 10-K / 10-K/A：按该报告期选择 FY，而不以
`filingDate` 推断财年。同一报告期有多个版本时，截至日期内最新的修订版优先，但需
保留原始 10-K 的 accession 供审计；10-Q 不能作为年度 filing 的静默替代。

### 2.2 资料与证据策略

原始 SEC HTML/iXBRL、附件和 Company Facts 必须完整保存为工件；模型上下文只使用
经过解析、来源验证、期间验证、单位验证后的有限 Evidence Artifact。10-K 正文中
的业务、风险和 MD&A 证据可以直接进入相应 Writer 章节，但不得绕过引用验证。

## 3. 目标数据流

```text
resolve_company
       |
discover_annual_filings
       |
       +-- download/parse/validate FY Y 10-K -----------+
       +-- download/parse/validate FY Y-1 10-K ---------+--> AnnualComparisonPack
       +-- fetch/select/validate Company Facts ----------+           |
       +-- discover/validate optional narrative evidence -+           v
                                                    Analysis Agent
                                                          |
                  validated business/risk/event evidence  +--> financial SectionAnalysisPack
                                  |                                      |
                                  +--> Section Writer <------------------+
                                                     |
                                                Final Writer
```

所有箭头传递类型化、不可变且带引用的工件；不传递 Research Agent 的自由文本总结。

## 4. Agent 与调度职责

### 4.1 确定性 DAG Scheduler（不是主 Agent）

Scheduler 负责创建节点、管理依赖、投递任务、并发上限、重试、缓存、恢复与事件落库。
它不访问 LLM，不解释财务数据，也不自行选择来源。PostgreSQL 继续是状态真相源，
Outbox 继续负责可靠事件投递。

### 4.2 Research Agent：受控 ReAct

Research Agent 只在 Coverage Ledger 存在可解释缺口时执行有限 ReAct：

1. Observe：读取已验证工件、缺口、预算与次数；
2. Reason：判断缺口是否影响已定义的下游节点；
3. Act：提出一个具体的下一步搜索、下载或解析请求；
4. Observe：由 Scheduler 写回验证结果；
5. 输出 `CONTINUE_SEARCH`、`READY_FOR_ANALYSIS`、`PARTIAL_READY` 或 `BLOCKED`。

停止条件由代码强制：必需覆盖满足、缺失已确认不可得、预算/时间/次数耗尽，或连续
搜索无新增有效证据。Agent 不得无限循环，也不得直接把未验证资料交给 Writer。

P07-06 将上述边界实现为不调用 LLM 的确定性策略：每类缺口最多补证一次，默认最多
作出 2 次补证决策、累计 120 秒、连续 1 次补证没有新增 `VALIDATED` 工件即停止。
策略只读取 Scheduler 写入的证据、次数、耗时和工具预算快照，不发起网络请求、不消耗
工具额度、不写节点状态；工具 Token 不适用于本阶段。目标 10-K 或两年 Facts 仍缺失时
必须 `BLOCKED`，仅上一年度 10-K 缺失而两年 Facts 完整时可 `PARTIAL_READY`。

### 4.3 Analysis Agent

确定性 Python/Decimal 工具选择与计算数值；Analysis Agent 只解释已验证的财务事实：
可比性、趋势、异常、披露因素、冲突和限制。它可输出 `EvidenceGapRequest`，但不能
自行调用搜索工具或重写原始数值。

### 4.4 Writer Agent

Writer 按章节消费工件：公司概览、业务、风险和已验证事件可直接来自 Evidence Bundle；
财务表现、关键指标和同比解释必须来自 Analysis Pack。最终摘要只读取已完成章节，且
必须通过引用、指标覆盖、必需章节和非投资建议门禁。

### 4.5 年度证据最小权限路由

P07-07 在 Analysis 与未来 Section Writer 之间设置确定性访问边界：已验证的
`FINANCIAL_FACT_SET` 与非阻塞 `AnnualComparisonPack` 只交给 Analysis；10-K、业务、
风险、MD&A 和事件类证据才可交给 Section Writer。`COMPANY_FACTS` 原始/筛选工件绝不
进入 Writer。路由必须再次核对比较包的财年、Company Facts 工件键和 checksum；不匹配、
补证未结束或 Research 已阻塞时，所有下游证据均被封锁。该层不读取正文、不生成章节、
不调用 LLM，也不修改调度状态。

P07-08 将已路由输入拆为财务表现、业务概览、风险因素和重大事件四个独立章节包。
财务包只能先交给 Analysis，财务章节草稿必须消费其 `SectionAnalysisPack`；业务、风险
和事件章节只消费各自白名单中的叙事证据。上一年度 10-K 缺失不影响目标年度业务概览，
但风险章节必须降级并禁止叙事同比；没有已验证重大事件时该章节为 `not_applicable`，
不是失败。本阶段只定义契约和就绪状态，不生成 Markdown 或接入调度器。

P07-09 只在财务、业务和风险三个核心章节均 `ready` 或 `partial` 后，才将经过
脱敏的章节草稿汇总为 Final Writer 输入；重大事件可以 `not_applicable`。核心章节缺失
或阻塞时拒绝最终汇总。缺引用、空内容或 partial 未披露限制等局部问题只允许一次章节
定向修订；额度耗尽后阻塞，不重新搜索、计算或循环调用模型。

P07-10 为上述未来 DAG 新增独立的节点表、依赖边和追加式事件账本；节点状态转换与
事件写入同一事务，超过十分钟的 `running` 节点可按剩余尝试次数恢复为可重试或终态。
诊断读模型只暴露状态、等待原因、稳定错误码、工件键、预算停止原因和关键路径；
Prometheus 与 trace 不得包含 job id、node key、公司名、提示词或 SEC 原文。它不接入
`annual_deep` 运行时，也不改动旧 `workflow_steps`、Outbox、API、Flow 或 Celery。

P07-10A 随后将 `annual_deep` 作为任务级模式持久化，并在既有单个 Celery Job 内选择
年度协调器。该协调器复用年度工件流水线的三路受限并发、Comparison Pack、受控补证、
最小权限路由、章节门禁与现有 Markdown/PDF 发布边界；legacy 仍走原 Flow。年度任务不
初始化或写入 legacy `workflow_steps`，其等待、失败和恢复只写年度节点账本。

P07-10B 将章节生成接入无工具的年度 LLM 适配器。财务 Analysis 只能接收确定性
Comparison Pack 与已验证的 Financial Fact Set；业务、风险和事件 Writer 只能读取其
SectionInputPack 授权的解析文本块。每个章节工件记录输入指纹、提示词版本、模型名、
引用白名单和状态；Final Writer 只编辑通过门禁的章节稿与限制说明，不读取 SEC 原文或
Company Facts。任何空输出、截断、投资建议、越权引用或一次定向修订后的持续失败都会
阻塞发布，而不是扩展搜索或伪造结论。

P07-11 为 `annual_deep` 与 legacy 新增独立的 `annual-paired` 评测入口：固定同公司、
同 `as_of_date`、同 deep 档位和仅 10-K 的范围，并交替两种模式的提交顺序。年度验收读取
年度节点图、Company Facts、Comparison Pack、章节工件、引用和限制说明，不将 legacy 的
`workflow_steps` 误作年度完成事实。年度 LLM Manifest 只聚合真实 usage、调用次数、耗时和
模型名；usage 缺失保持 null。首轮 AMZN/JPM × 2 次仅验证工具、关键路径与安全降级，绝不
据此声称性能收益；真实运行说明见 `docs/25-P07-11-ANNUAL-BENCHMARK.md`。

P07-12 仅在 Streamlit 产品入口和诊断视图中展示年度模式：创建页固定
`annual_deep` 为 `deep + 10-K`，详情页使用年度节点、依赖等待、预算停止原因和关键路径
解释进度，不复用 legacy `workflow_steps`。工件页按证据、财务比较、章节产物和运行状态
分类；原始 SEC 文件与全文解析结果不被自动下载或展示。

## 5. 新契约和状态

后续实现应新增而非滥用固定 `workflow_steps`：

- `ResearchNode`：可执行 DAG 单元及其稳定 idempotency key；
- `NodeDependency`：节点之间的 fan-out/fan-in 前置条件；
- `EvidenceArtifact`：来源、fiscal year、locator、checksum、解析版本、验证状态；
- `CoverageLedger`：必需项、满足状态、缺失原因和可消费下游；
- `ResearchDecision`：受控 ReAct 决策；
- `ResearchObservation`、`SupplementRequest`、`ResearchDecisionBudget`：补证观察快照、
  允许的单步请求与硬上限；
- `AnnualComparisonPack`、`SectionAnalysisPack`、`SectionDraft`。

节点状态为 `pending`、`running`、`succeeded`、`failed_retryable`、
`failed_terminal`、`blocked`、`cancelled`；工件状态为 `produced`、`validated`、
`consumed`、`superseded`。所有 Artifact 必须不可变、可版本化、可审计。

## 6. 架构决策

1. 保留三个核心 Agent，不新增自由指挥其他 Agent 的主 Agent。
2. Plan-and-Execute 用于顶层 DAG 计划；首版年度任务图由确定性模板生成，不引入
   Planner LLM。
3. ReAct 仅用于 Research 补证与覆盖决策，不负责工作流状态迁移。
4. RAG 指“从已验证 SEC 工件选择相关证据块”，不是把全文或全量 Company Facts
   塞进模型上下文。
5. Reflection 保持有界：证据覆盖、数字一致性和引用优先用确定性门禁；Writer 修订
   与补证都有严格次数上限。
6. 不先声称性能提升。P07-11 必须以相同样本、模型、配置和网络条件测量关键路径、
   E2E 耗时、Token、质量与失败率。

## 7. 实施顺序

P07-01 到 P07-12 的定义见 `docs/05-DEVELOPMENT-ROADMAP.md`。任何运行时切换必须
保留当前 legacy 顺序路径，直到 P07 fixture、故障注入、恢复和对照基准全部通过。
