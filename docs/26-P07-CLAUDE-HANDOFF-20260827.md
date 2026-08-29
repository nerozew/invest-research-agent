# Claude 交接：P07 年度运行时修复与分阶段测试

更新时间：2026-08-27。用户主动暂停 Codex，要求交给 Claude 接续。

**交接结论：代码已经落在当前本地工作区；不是从零实现 P07。当前停在第三步“完整年度协调器离线集成测试”，最新结果为 6 passed / 1 failed。不要立即启动真实付费 canary。**

## 1. 从哪里接手

- 项目绝对路径：`D:\MyProjects\invest_research_agent`。
- 系统与终端：Windows / PowerShell。
- Python：项目 `.venv\Scripts\python.exe`；项目要求 Python 3.12+，依赖见 `pyproject.toml`、`uv.lock`。
- 当前分支：`agent/m2-deterministic-tools`。
- 当前 HEAD：`52bf4f7 feat(p06-11m-n): deterministic metrics and writer observability`。
- **工作区有大量已修改文件和未跟踪文件，P07 的许多源码与测试仍是未跟踪文件。不能只看 HEAD，也不能只用普通 git diff 判断实现范围。**
- 本轮未提交、未推送，也未重新 fetch 验证 GitHub 状态。不要声称 GitHub 已包含本地 P07 实现。
- 接手应在同一工作目录操作。若转到另一台机器/Claude 云环境，只克隆 GitHub 不够：需要用户安全传递包含未跟踪文件的源码快照；不要上传 `.env`、密钥、数据库备份或原始诊断载荷。
- 暂停后 Codex 只整理本交接文档，没有继续修代码、跑测试、启动服务或发起付费任务。最后一次测试命令已退出，无正在由 Codex 等待的测试 session。

## 2. 业务目标与不能改变的边界

目标是缩短年度投研耗时，同时保持数值可信、证据可追溯、失败可解释；不是让三个 Agent 无限制自由协商。

- `legacy` 保留原流程；`annual_deep` 是独立年度运行模式，目前已经接 API/Worker，不再是早期的 `MODE_NOT_ENABLED` 阶段。
- 年度目标从截至 `as_of_date` 可用的最新 10-K 的 SEC `reportDate` 判断，不按提交年份猜财年；不能用 10-Q 静默代替 10-K。
- 目标 10-K + 两年 Company Facts 是财务链路的基础证据；缺上年 10-K 时可以交付有限财务比较，但不能做上年风险/MD&A 叙事同比。
- 指标用严格事实筛选和 Decimal 确定性公式；LLM 负责解释、归因、章节写作和编辑，不负责重算指标或自由补充事实。
- 两份 filing 与 Company Facts 最大三路 I/O 并发；章节阶段单 Job 内最多三个模型工作单元。财务写作依赖财务分析，业务/风险可并行。
- Writer 只能看所属章节的授权证据；Final Writer 只能看通过门禁的章节，不看 SEC 原始材料。
- 缺核心证据应安全阻塞，不发布伪完整报告；部分报告必须披露限制。
- 保留现有 Celery Job、Outbox、legacy workflow_steps；本次可靠性修复不重构框架、不加新数据库迁移、不改研究范围。

### 必须区分“目标设计”与“当前实际实现”

P07-06 是确定性补证决策器，不是真正调用模型的 ReAct 搜索循环。目前 `AnnualResearchRuntime.run()` 首轮获取后若收到 `CONTINUE_SEARCH`，会用同一账本、`attempted_requirements`、`decisions_used=1`、`consecutive_no_gain=1` 再决策，**不会真正执行一次额外补证搜索**。不能把它描述为“已完成真实有界检索执行器”。是否补真实执行属于后续明确范围，不应在修停止事件时偷偷引入网络调用。

## 3. 已有什么实现

以下模块均已在本地存在，路线图标记多数 P07 阶段完成；这代表模块实现，不代表真实年度报告已全面验收。最近整链路测试正在揭示模块之间的接线问题。

| 阶段 | 本地实现 |
|---|---|
| P07-00/01 | ADR、年度领域契约、DAG 节点状态与证据覆盖账本 |
| P07-02 | 目标/上年 10-K、修订版、截止日年度选择器 |
| P07-03 | 每份 filing 下载、解析、checksum、Manifest 与恢复 |
| P07-04 | 10-K×2 与 Company Facts fan-out/fan-in |
| P07-05 | 严格 canonical accession 筛选、10 项 Decimal 财务指标与比较包 |
| P07-06/07 | 补证策略与最小权限证据路由 |
| P07-08/09 | 四类章节契约、章节计划、最终门禁与一次定向修订 |
| P07-10 | 独立年度节点/依赖/事件表，状态机、恢复、关键路径、指标/span |
| P07-10A/10B | 年度运行协调器、模式分流、章节 LLM 适配器、受限 Final Writer、发布 |
| P07-11 | annual-paired 评测工具和固定数据集；真实基准未完成 |
| P07-12 | Streamlit 年度模式入口、节点与等待诊断、年度工件分类 |

### 真实 canary 的历史事故

此前首个 AMZN 年度任务因关键路径混用 naive/aware datetime 失败，任务详情轮询也出错；8 任务评测没有完成。旧计划 run id 为 `p07-11-annual-canary-20260827`。旧失败任务只作 incident 证据，不计成功，不可拿旧幂等键复用为新一轮结果。

参考 [基准说明](D:/MyProjects/invest_research_agent/docs/25-P07-11-ANNUAL-BENCHMARK.md) 文末事故记录。该文档开头和路线图仍有“canary 未执行”的历史表述，准确说法应是“尝试过但失败，完整基准未完成”。交接时未修改这些历史文档。

## 4. 本次修复/测试推进到哪里

### 第一步：UTC 与 Worker 启动恢复——已做

- 年度节点数据库时间统一按 UTC 处理：naive 值按 UTC 解释，aware 值转换 UTC。
- 关键路径、运行中节点耗时、恢复阈值及事件时间使用一致语义。
- Worker stale-job recovery 在事务提交前提取 UUID，提交后不再访问过期 ORM 对象。
- 还修复恢复失败日志分支残留的 `job.id` 访问，避免日志处理本身触发 `DetachedInstanceError` 并中断后续任务收口。
- 补齐 UTC/非 UTC、10 分钟阈值、恢复失败继续处理、真实步骤收口等测试。
- 已为 CI 增加独立 Worker 恢复离线测试入口，因为原离线命令排除 `test_worker*`。

### 第二步：真实 PostgreSQL 与迁移回归——已做

- `tests/conftest.py` 增加隔离 PostgreSQL 数据库、真实迁移 Session，以及 SQLite/PostgreSQL 双后端参数化 fixture。
- Session 使用生产相近配置 `autoflush=False, expire_on_commit=True`。
- 修复 Alembic 测试被实际 `DATABASE_URL` 误导的风险；测试库独立创建与清理，不碰用户业务库。
- 新增 `tests/test_migration_annual.py`：年度表与 research_mode 迁移、legacy 回填、降级/升级保留旧数据、约束检查。
- 修复生产缺陷：同一事务连续追加恢复事件时，由于 autoflush=False，未 flush 的前一条事件没计入 MAX(event_no)，导致重复序号、UniqueViolation 与整事务回滚。在 `SqlAnnualNodeStore._append_event()` 查询序号前 `session.flush()`，不是 commit。
- 加入失败注入，确认状态与事件仍原子回滚。
- API 年度节点快照以真实 PostgreSQL 的 UTC/Asia-Shanghai 时间验证。
- 隔离 `DIAGNOSTIC_CAPTURE_MODE` 对测试默认值的污染；一个 Settings 默认值测试改用 `_env_file=None`，没有改用户 `.env`。

第二步结束时实际运行记录（**不是最新第三步代码的全量结果**）：

- 相关数据库/迁移/API/恢复组合：60 passed。
- CI 同款 PostgreSQL 筛选命令：13 passed / 29 deselected。
- Worker 离线恢复：3 passed / 3 skipped。
- 全量离线：1464 passed / 31 skipped，另有一个既有 Starlette HTTP 422 弃用警告。
- 当时的定向 Ruff、mypy 和 git diff --check 通过。
- 当时测试容器与隔离数据库已清理。交接时未重新启动或检查项目服务健康。

### 第三步：真实年度协调器的离线集成——进行中，用户要求暂停

新增 [tests/test_annual_runtime.py](D:/MyProjects/invest_research_agent/tests/test_annual_runtime.py)。它不是把整个协调器 mock 掉，而是运行真实选择器、文档/Facts 工件、fan-in、比较、路由、章节规划、门禁、数据库节点与报告发布；仅 SEC 工具与模型 completion 使用固定响应。

测试通过 socket.connect/connect_ex 拦截意外联网；数据库为测试临时目录中的 SQLite 文件，各线程独立连接，不是共享一条连接的 StaticPool。报告使用真实 Markdown/PDF 渲染，并用 PyMuPDF 确认可打开、有页面。

已加入七项测试（核心缺证据参数化占三项）：

1. 完整流程：三路 I/O 与分析/业务/风险通过 Barrier 确认并发启动，检查财务写作依赖、最小权限输入、节点全部成功、Markdown/PDF 存在、比较包有十项指标。
2. 缺上年 10-K：部分报告发布、限制说明、停止原因诊断。
3. 无目标 filing、缺上年 Facts、缺目标年 Facts：安全阻塞，无模型调用、无最终报告、节点不残留 pending/running。
4. 首次业务章节缺引用：只修订业务章节一次，其他章节不重写。
5. 在 Final Writer 之前中断再执行：已完成章节和有效证据工件复用，不重复章节模型调用/下载。

本步已修改 `annual_runtime.py`：

- `_node()` 在节点已经 succeeded 的分支也解包 `_AnnualNodeOutput`，修复定向修订后传给门禁的对象类型错误。
- `_record_decision()` 不再把 `CONTINUE_SEARCH/supplement_required` 当作 budget_stop 写入，从而避免缺证据路径直接 ValueError；但停止原因白名单还没补全，见下一节。
- 增加 `_block_remaining()`，在证据拒绝或最终质量拒绝的 fan-in 边界封锁未终态节点；成功节点不回退。

**暂停时最后一次实际测试结果：6 passed / 1 failed（约 4.6 秒测试时间）。之后没有改代码、重跑或执行本步静态检查。新测试文件的 import/格式/行长仍需 Ruff 检查。**

## 5. 现在最先修什么：唯一红测的准确根因

失败用例：

```text
tests/test_annual_runtime.py::test_missing_comparator_publishes_partial_with_budget_stop
assert h.progress.snapshot(job_id=h.job_id).budget_stop_reasons
AssertionError: assert ()
```

部分报告的生成和发布已成功，失败的是最后的停止原因诊断断言。

直接原因链：

1. 缺上年 10-K，第一次策略返回 CONTINUE_SEARCH。
2. Runtime 第二次观察中包含该缺口的 `attempted_requirements`。
3. `AnnualResearchDecisionPolicy._stop_code()` 优先判断“已经尝试”，所以返回 **`SUPPLEMENT_ALREADY_ATTEMPTED / supplement_already_attempted`**，不是 `no_evidence_gain`。
4. 本步更新的 Runtime `_record_decision()` 没把这个码纳入允许集合，因此未写事件。
5. SQL store `_BUDGET_REASON_CODES` 也没包含该码；只改 Runtime 会再次触发 ValueError。

建议最小修复：对齐策略终态停止码、Runtime 记录条件和 SQL 白名单；保留 `supplement_required` 不属于停止事件的边界；不要改变策略优先级来迎合测试，不要把正确的停止原因伪造为另一种原因。新增精确断言，确认事件/快照记录 `supplement_already_attempted`，而不只是断言非空。

**诊断更正：Codex 之前口头提到“最近事件窗口挤掉停止原因”，不是当前红测已经证实的直接根因。** 另有一个独立代码风险：`SqlAnnualNodeStore._snapshot()` 只从最近 50 条事件提取 budget_stop_reasons，长期运行可能丢失早期原因。若本步处理，应先补“早期停止事件 + 超过 50 条后续事件”的失败测试，再做独立修复；不要与当前白名单遗漏混淆。

相关源码定位（行号可能随修改漂移，以函数名检索）：

- `infrastructure/annual_runtime.py`：`_record_decision()`、`_block_remaining()`、`_node()`。
- `domain/annual_research_policy.py`：`_stop_code()`，尤其 attempted_requirements 优先于 consecutive_no_gain。
- `infrastructure/db/annual_node_store.py`：`_BUDGET_REASON_CODES`、`record_budget_stop()`、`_snapshot()`。
- `infrastructure/observability/annual_nodes.py`：`record_annual_budget_stop()`。

## 6. 下一步按小步执行，不要一次扩大范围

### A. 先把当前七项集成测试收绿

1. 读本交接、工作区状态及上述函数，确认接续的是同一份未提交代码。
2. 运行当前七项测试复现 6/1（环境变化时如实报告差异）。
3. 修停止码对齐，补精确原因断言；单独测试事件窗口风险。
4. 对本步新增/修改文件执行 Ruff，清理新增测试格式与未用 import，mypy 检查生产源码。

### B. 继续补恢复与失败路径，不要用当前绿测声称全覆盖

以下是待验证点，不是都已经用测试证实的缺陷：

- 目前恢复测试只覆盖“所有章节已完成、Final Writer 尚未开始”时中断；没有覆盖 RUNNING 节点僵尸、某章节写工件后尚未提交成功状态、分析完成但财务写作未完成等断点。
- Runtime 节点定义默认 max_attempts=1；应核对是否真的支持预期的僵尸重试。不要随意覆盖已有 Job 的图定义，`create_graph` 会校验定义一致。
- `_node()` 对 SUCCEEDED 节点仍执行传入函数；章节有缓存，但身份解析/filing 选择、最终编辑的跨进程复用不一定成立。当前恢复测试没有证明“所有成功节点都零外部调用”。
- `_read_section()` 校验输入指纹、提示词版本和工件白名单，但目前没有比较模型配置，也没有完整验证缓存嵌入输入是否等于本次输入；需补模型变化、输入变化、损坏缓存与越权引用的实际回归。
- 财务分析尚没有独立输出缓存；完成章节中才嵌入分析结果。中断窗口应通过测试厘清。
- 缺引用可以到最终门禁触发一次修订；伪造引用、截断、空输出、投资建议会更早在 LLM 适配器抛错。不要宣称所有这些错误都已经实现一次定向修订。
- 首次修订成功已测；应补“第二次仍缺引用必须阻塞、不调用 Final Writer、不发布报告、节点终态正确”。
- Final Writer 提示词要求不能新增数字/事实，但当前校验主要检查引用、标题、文本与投资建议；不能把提示词约束当成已实现的确定性事实一致性校验。
- 新完整流程测试只检查 Comparison Pack 的指标数量，尚未证明最终正文逐项保留全部指标、数值无篡改；需单独补数字门禁测试。
- Barrier 证明这三项确实并行，不等于已用活动调用计数器证明所有路径最大并发始终为 3；事件章节有材料的路径尚需覆盖。
- 完整集成目前直接调用 Runtime + Publisher，不是 API→Celery→Worker 的容器端到端；真实 Job 状态/工件数据库登记仍需后续接线 smoke 验证。

每次只选一类断点：先红测、再小修、再回归。若涉及扩大 ReAct 执行能力、改变节点图/迁移或重构重试模型，先向用户说明范围，不把它夹在当前诊断修复中。

### C. 离线回归与 PostgreSQL 回归

先定向，再 P07 与关联测试，再全量离线。需要真实数据库的测试使用测试容器/专用测试库，不把 TEST_DATABASE_URL 指向业务库。

### D. 服务验证，最后才考虑真实 canary

待代码与离线/PG 测试收口，再重建 API/Worker 并检查健康、队列、源码版本；服务启动前明确是否会消费已有 live 队列。

真实 canary 需要用户重新明确授权，使用新 run id：AMZN/JPM × 两种模式 × 两次重复 = 8 串行任务；deep、仅 10-K、max_failures=2、max_total_tokens=500000、timeout_seconds=600。不得自动扩大样本、复用事故任务或生成简历级提速结论。usage 缺失仍是 null，不算零成本。

## 7. 源码与文档地图

下表路径均相对项目根 `D:\MyProjects\invest_research_agent`；首要入口为 [annual_runtime.py](D:/MyProjects/invest_research_agent/src/invest_research/infrastructure/annual_runtime.py)。

| 位置 | 内容 |
|---|---|
| `docs/01-PRD.md`、`02-ARCHITECTURE.md`、`05-DEVELOPMENT-ROADMAP.md` | 产品需求、架构、整体路线；勾选不等于当前集成验收全绿 |
| `docs/24-P07-ANNUAL-PIPELINE-ADR.md` | 年度业务边界、阶段职责与决策 |
| `docs/25-P07-11-ANNUAL-BENCHMARK.md` | 基准命令、证据标准与 incident |
| `docs/10-RUN-GUIDE.md`、`14-TASK-HANDOFF.md` | 既有运行与历史交接资料；本次进度以本文件为准 |
| `src/invest_research/domain/annual_*.py` | 年度契约、filing 选择、路由、补证策略、章节与状态机 |
| `src/invest_research/domain/models.py` | 请求、财务事实、指标、报告等共享契约 |
| `src/invest_research/infrastructure/annual_runtime.py` | 本轮重点：年度协调器、节点接线、章节缓存与并发、状态工件 |
| `src/invest_research/infrastructure/annual_llm_writing.py` | 年度模型适配器、授权上下文/引用、章节与 Final Writer 校验 |
| `src/invest_research/infrastructure/annual_document_pipeline.py` | 单 filing 原始/解析/Manifest 工件 |
| `src/invest_research/infrastructure/annual_company_facts_pipeline.py` | Facts 原始/筛选/Manifest 工件 |
| `src/invest_research/infrastructure/annual_evidence_fanout.py` | 三路证据并发与账本生成 |
| `src/invest_research/infrastructure/annual_comparison_builder.py`、`financial/annual_comparison.py` | 年度比较包构建与严格确定性指标 |
| `src/invest_research/infrastructure/annual_evidence_router.py`、`annual_section_planner.py` | 下游最小权限路由与章节计划 |
| `src/invest_research/application/annual_finalization_gate.py` | 章节最终门禁与一次定向修订 |
| `src/invest_research/application/annual_node_progress.py` | 节点存储/观测服务入口 |
| `src/invest_research/infrastructure/db/annual_node_store.py` | 年度节点 SQL 状态机、事件、恢复、快照与关键路径 |
| `src/invest_research/infrastructure/db/models.py` | ORM 模型 |
| `migrations/versions/0009_annual_node_runtime.py`、`0010_research_job_mode.py` | 年度节点及任务模式迁移 |
| `src/invest_research/infrastructure/queue/worker.py` | Celery Worker、stale job recovery、运行分流 |
| `src/invest_research/infrastructure/queue/execution_recorder.py` | 执行结果/工件登记 |
| `src/invest_research/infrastructure/flow_wiring.py` | legacy 与年度运行的组装接线 |
| `src/invest_research/api/app.py`、`application/execution.py` | API 与任务执行服务 |
| `src/invest_research/tools/sec_submissions.py`、`sec_company_facts.py`、`sec_downloader.py`、`artifact_store.py` | SEC 与工件工具 |
| `src/invest_research/reporting/artifact_publisher.py`、`renderer.py`、`pdf.py` | 最终 Markdown/PDF 发布边界 |
| `src/invest_research/infrastructure/observability/annual_nodes.py`、`metrics.py` | 年度 span 与低基数指标 |
| `frontend/`、`src/invest_research/frontend/` | Streamlit 页面及前端 DTO/展示逻辑 |
| `scripts/run_live_portfolio_benchmark.py`、`evals/annual_benchmark_dataset.json` | annual-paired 工具与固定数据集 |
| `tests/test_annual_runtime.py` | 本轮新加的真实组件离线集成测试，当前一项失败 |
| `tests/test_annual_*.py` | P07 各组件回归 |
| `tests/test_worker_stale_recovery.py`、`test_migration_annual.py`、`test_api_get_job.py` | 前两步恢复/迁移/API 回归 |
| `tests/conftest.py`、`.github/workflows/ci.yml` | 测试环境隔离、PG fixtures、CI 命令 |
| `compose.yml`、`Dockerfile`、`deploy/` | 部署与观测配置；不要清 volume |

### 运行数据位置

- 宿主默认工件根：`D:\MyProjects\invest_research_agent\artifacts`，可由 ARTIFACT_ROOT 配置改变；Compose 内部路径为 `/app/artifacts`，具体挂载看 compose.yml。
- 单 Job 常见工件：`<artifact_root>/<job_id>/annual/<accession>/source.html|pdf`、`parsed.json`、`manifest.json`；`annual/company-facts/source.json`、`selected.json`、`manifest.json`；`annual/comparison.json`；`annual/sections/<section>.json`；`annual/runtime_state.json`；`08_report.md`、`09_report.pdf`。
- 评测设计输出：`evals/annual_benchmark_runs/<run_id>/`；交接只读列举时未得到此目录下已有运行子目录，**不要声称已有完整的年度 canary summary/report**。
- `evals/live_runs/`、`evals/runs/`、`reports/`、`backup/` 中有历史数据，保留，不把历史 fake/legacy 结果混入年度实测。
- 新集成测试用 pytest 临时目录，产物不是正式 benchmark 数据。

## 8. 可复制的验证命令

只在 Claude 接手后按步骤运行；本交接阶段没有执行以下新测试。

```powershell
Set-Location 'D:\MyProjects\invest_research_agent'
git status --short
git branch --show-current
git log -1 --oneline

# 先复现当前失败；不需要项目 API、Redis 或 Worker。
$env:SKIP_DB_TESTS = '1'
$env:FLOW_MODE = 'fake'
$env:DATABASE_URL = 'sqlite+pysqlite:///:memory:'
.venv\Scripts\python.exe -m pytest tests/test_annual_runtime.py -q -p no:cacheprovider --tb=short

# 年度组件 + API/benchmark 关联回归。
$annualTests = (Get-ChildItem tests -Filter 'test_annual_*.py').FullName
.venv\Scripts\python.exe -m pytest @annualTests tests/test_api_get_job.py tests/test_live_portfolio_benchmark.py -q -p no:cacheprovider --tb=short

# Worker 恢复独立跑：下一条全量命令会排除 test_worker*。
.venv\Scripts\python.exe -m pytest tests/test_worker_stale_recovery.py -q -p no:cacheprovider --tb=short

# 完整离线回归。
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --ignore-glob='*test_worker*' -m 'not docker' --tb=short

# 定向静态检查；修完后再执行 CI 口径的全量静态检查。
.venv\Scripts\python.exe -m ruff check src/invest_research/infrastructure/annual_runtime.py src/invest_research/infrastructure/db/annual_node_store.py tests/test_annual_runtime.py
.venv\Scripts\python.exe -m mypy src/invest_research/infrastructure/annual_runtime.py src/invest_research/infrastructure/db/annual_node_store.py
.venv\Scripts\python.exe -m ruff check src tests migrations scripts
.venv\Scripts\python.exe -m mypy src
git -c core.safecrlf=false diff --check
```

PostgreSQL 用全新终端或清除 SKIP_DB_TESTS，Docker 正常时 fixture 可自建测试容器。先确认没有把 TEST_DATABASE_URL 配到真实业务库，也不要输出含口令的连接串：

```powershell
Remove-Item Env:SKIP_DB_TESTS -ErrorAction SilentlyContinue
$env:FLOW_MODE = 'fake'
.venv\Scripts\python.exe -m pytest tests/test_migration_annual.py tests/test_annual_node_runtime.py tests/test_worker_stale_recovery.py tests/test_api_get_job.py -q -p no:cacheprovider --tb=short
```

注意：`git diff --check` 不检查未跟踪新文件的全部内容，不能代替对新测试/源码的 Ruff 检查。当前 P07 多数文件未跟踪。

## 9. 操作约束与交付口径

- 不提交、不推送、不重置用户修改；不要 `git reset --hard`、`git clean` 或覆盖未跟踪 P07 文件。
- 不清理 artifacts、失败 Job、Redis、数据库、备份或 Docker volume；尤其禁止 `docker compose down -v`。
- 不修改 `.env`，不打印 Key/Authorization/完整连接串；诊断和 metrics 标签不得包含 Prompt、SEC 原文、公司名或 Job ID 等高基数/敏感载荷。
- 本阶段不自动调用 SEC/LLM，不跑付费 benchmark。即便历史消息授权过 canary，本次恢复修复后仍先让用户确认新一轮。
- 不引用“1464 passed”作为最新代码已全绿的证明；它是第二步结束时的基线。本轮只有最新 6/1。
- 不把源码中的架构目标当成运行证据；按“已经实测 / 已落代码待验证 / 推测风险”分别汇报。
- 每个修复给出：具体失败 → 根因 → 最小修改 → 测试结果 → 剩余事项。当前任务是可靠性收口，不是继续堆功能。

## 10. 可直接给 Claude 的提示词

```text
请接手 D:\MyProjects\invest_research_agent 的 P07 年度运行时修复任务。
先完整阅读 docs/26-P07-CLAUDE-HANDOFF-20260827.md，并核对本地工作区，而不是只看 Git HEAD。

当前分支 agent/m2-deterministic-tools，HEAD 52bf4f7；大量 P07 源码/测试未提交且未跟踪，请全部保留，不提交、不推送、不重置。
前两步 UTC/Worker 恢复与真实 PostgreSQL 回归已做；现在第三步真实组件的离线集成测试停在 tests/test_annual_runtime.py，最新结果 6 passed / 1 failed。

优先复现 test_missing_comparator_publishes_partial_with_budget_stop。直接根因是策略返回 supplement_already_attempted，但 Runtime 和 SQL 存储的停止事件白名单未对齐。不要把它改成 no_evidence_gain 来掩盖问题，也不要把 supplement_required 当作停止事件。最近 50 条事件可能漏掉早期停止原因是另一个待验证风险。

先用最小修改修复这项红测并补精确断言，然后按交接文档逐步补失败/恢复测试，再执行 P07、Worker、全量离线、PostgreSQL 与 Ruff/mypy 回归。新测试文件尚未经过本轮静态检查。恢复测试目前只覆盖 Final Writer 前中断，不代表任意断点都已验证。

不启动真实付费 canary，不修改 .env，不清数据或 Docker volume，不扩大 ReAct/Agent/数据库设计。需新增检索执行、改节点图或扩展重试语义时，先说明范围。真实 canary 以后需我明确授权并换新 run id。

请先简要复述你确认的当前状态与本轮小目标，再开始修复；每步明确区分已验证结论和待验证风险，最后给修改文件、真实测试结果和剩余事项。
```
