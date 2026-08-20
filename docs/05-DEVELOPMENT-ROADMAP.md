# 细粒度开发路线

## 1. 使用规则

- 每次只做一个任务；不要让 Cline 连续完成一整个 Phase。
- 单个任务目标控制在 30–90 分钟的学习粒度。遇到超出范围的工作，拆出新任务，不在当前任务顺手扩张。
- 任务完成必须同时满足：产物存在、验收测试通过、无相关静态检查错误、学习卡片已输出。
- 未完成任务 ID 保持 `Pxx-xx`；验收真实通过后，仅把该行 ID 改为 `Pxx-xx ✅`。最早一个不带 `✅` 的 ID 就是下一候选任务。
- 表格中的耗时只是任务粒度参考，不是承诺。调试本身也是学习内容。
- 只有测试真实通过才能勾选；禁止把“预计通过”写成已通过。

## 2. 每个任务的固定交付格式

Cline 完成任务后必须停止，并给出：

1. 本任务目标（1 句话）。
2. 修改/新增文件清单。
3. 关键实现思路（最多 5 点）。
4. 实际执行的验证命令与结果。
5. 我需要掌握的 3 个知识点。
6. 一个让我用自己的话回答的检查问题。
7. 已知限制、风险和下一任务建议。

## Phase 0：项目护栏与最小工程骨架

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P00-01 ✅ | 阅读 7 份设计文档，列出矛盾和待确认假设 | `docs/08-OPEN-QUESTIONS.md` | 只写问题，不生成代码 | 需求澄清、架构假设 |
| P00-02 ✅ | 初始化 Git 与基础 `.gitignore` | `.git/`、`.gitignore` | 密钥、缓存、工件、虚拟环境不被跟踪 | Git 安全边界 |
| P00-03 ✅ | 用 `uv` 建立 Python 3.12 项目 | `pyproject.toml`、`src/`、`tests/` | 可导入包，最小测试通过 | src layout、依赖锁定 |
| P00-04 ✅ | 添加格式、lint、类型和测试配置 | Ruff、mypy/pyright、pytest 配置 | 四类命令均可运行 | 质量工具分工 |
| P00-05 ✅ | 建立配置对象和 `.env.example` | `settings.py`、`.env.example` | 缺必需变量时报可读错误；无真实密钥 | 12-factor 配置 |
| P00-06 ✅ | 建立统一命令入口 | Makefile/PowerShell scripts 或 `pyproject` scripts | 一条命令运行 lint + test | 可重复开发流程 |

## Phase 1：领域模型与状态持久化

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P01-01 ✅ | 定义 Job/Step 状态枚举和错误码 | `domain/status.py`、`errors.py` | 合法/非法状态测试通过 | 状态机、错误分类 |
| P01-02 ✅ | 实现 `ResearchRequest` 和公司身份模型 | Pydantic models | 日期、空输入、CIK、语言边界测试 | Pydantic v2 校验 |
| P01-03 ✅ | 实现来源、申报、文档模型 | domain models | JSON round-trip 测试 | 不可变数据契约 |
| P01-04 ✅ | 实现财务事实和指标模型 | `FinancialFact`、`MetricResult` | Decimal、单位、期间校验通过 | 财务数据口径 |
| P01-05 ✅ | 实现 Research/Analysis/Report/Quality pack schema | 4 组 models | 必需字段和 version 测试 | 结构化 Agent 输出 |
| P01-06 ✅ | 编写纯函数 Job/Step 状态转换器 | state transition service | 非法转换被拒绝 | 显式状态机 |
| P01-07 ✅ | 启动 PostgreSQL 测试容器与 SQLAlchemy base | DB config、health test | 集成测试能建连并回滚事务 | 连接池、测试隔离 |
| P01-08 ✅ | 建 Alembic 并迁移 company/job/step 表 | migration 001 | upgrade/downgrade/upgrade 均通过 | 可逆迁移 |
| P01-09 ✅ | 迁移 source/filing/document 表 | migration 002 | 约束和索引集成测试 | 数据规范化 |
| P01-10 ✅ | 迁移 fact/metric/report/citation 表 | migration 003 | Decimal 精度和 FK 测试 | 事实与派生数据分离 |
| P01-11 ✅ | 迁移 invocation/artifact 表 | migration 004 | 幂等唯一约束测试 | 审计与幂等 |
| P01-12 ✅ | 实现 Job repository | repository + tests | create/get/status 条件更新通过 | repository、事务边界 |
| P01-13 ✅ | 实现 Step/Artifact repository | repositories + tests | 原子成功提交、重复写拒绝 | 一致性与恢复 |

## Phase 2：确定性工具层

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P02-01 ✅ | 建立 Tool Protocol、统一结果和错误对象 | `tools/base.py` | fake tool 契约测试 | 依赖倒置、typed tools |
| P02-02 ✅ | 建立共享 httpx client 与显式 timeout | HTTP adapter | timeout、状态码映射测试 | HTTP 生命周期 |
| P02-03 ✅ | 实现 SEC User-Agent 和全局限流器 | SEC client middleware | 模拟时钟下不超过配置速率 | 公平访问、token bucket |
| P02-04 ✅ | 实现 `CompanyResolverTool` | tool + SEC ticker fixture | MSFT → 10 位 CIK；歧义返回候选 | 实体解析 |
| P02-05 ✅ | 实现 `SECSubmissionsTool` | tool + recorded fixture | 截止日过滤、10-K/10-Q 选择测试 | SEC submissions 格式 |
| P02-06 ✅ | 实现 `SECCompanyFactsTool` | tool + fixture | taxonomy/concept/unit/period 保真 | XBRL facts |
| P02-07 ✅ | 实现 URL 规范化与来源去重 | pure functions | tracking 参数、片段、重复 URL 测试 | canonicalization |
| P02-08 ✅ | 实现 `FilingDownloaderTool` | downloader + fake HTTP tests | 大小、类型、重定向、checksum 校验 | 安全下载、流式 I/O |
| P02-09 ✅ | 实现 SEC HTML 解析器 | parser + fixture | 标题、章节、表格文本和 locator 保留 | HTML/iXBRL 结构 |
| P02-10 ✅ | 实现 PDF 解析器主路径 | PyMuPDF 主路径 + tiny fixture（见 docs/02 §5.3/ADR-005） | 页码、空页和解析错误测试 | PDF 文本边界 |
| P02-11 ✅ | 实现 PDF/HTML 解析降级路由 | parser router | 主解析失败后只调用一次备用解析器 | fallback pattern |
| P02-12 ✅ | 实现本地 `ArtifactStoreTool` | atomic local store | 临时写、checksum、读取、重复写测试 | 原子文件操作 |
| P02-13 ✅ | 实现财务 concept 映射配置 | versioned mapping YAML/JSON | 同义 concept 选择测试 | XBRL 扩展与口径 |
| P02-14 ✅ | 实现可比期间选择器 | pure functions | 52/53 周、季度/YTD、修订申报 fixtures | 期间可比性 |
| P02-15 ✅ | 实现 5 个利润与增长指标 | calculator + tests | 正常、负数、零分母、缺失值测试 | Decimal、公式版本 |
| P02-16 ✅ | 实现 5 个资产负债/现金流指标 | calculator + tests | 正常、单位冲突、不可计算测试 | 财务比率 |
| P02-17 ✅ | 实现 `GoogleSearchTool` provider interface | interface + fake provider | 查询、as-of、分页、去重契约通过 | anti-corruption layer |
| P02-18 ✅ | 实现 Serper provider adapter | adapter + mocked contract test | 请求/响应映射和认证脱敏 | 第三方 API 封装 |
| P02-19 ✅ | 实现 `CitationVerifierTool` | verifier + tests | claim/source/locator/数字引用测试 | 可追溯性 |

## Phase 3：CrewAI Agent 与 Flow

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P03-01 | 封装可配置 DeepSeek LLM factory | LLM config adapter | 不打印 key；fake model 配置测试 | OpenAI-compatible API |
| P03-02 | 写信息搜集 Agent 提示词 v1 | versioned prompt | 输入/输出/禁止项齐全，prompt snapshot | Agent 角色边界 |
| P03-03 | 写财报分析 Agent 提示词 v1 | versioned prompt | 明确只能调用计算器取数 | 防止 LLM 算术 |
| P03-04 | 写报告 Agent 提示词 v1 | versioned prompt | 引用键、事实/分析分离、免责声明 | grounded generation |
| P03-05 | 用 fake LLM 构建 Research Task | task + unit test | 输出能解析为 `ResearchPack` | Task、output_pydantic |
| P03-06 | 用 fake LLM 构建 Analysis Task | task + unit test | 只暴露允许的分析工具 | least privilege tools |
| P03-07 | 用 fake LLM 构建 Writer Task | task + unit test | 只使用上游 context，不引入新事实 | context control |
| P03-08 | 组合三个 Task 为 sequential Crew | crew factory + test | 调用顺序严格为 1→2→3 | CrewAI Process |
| P03-09 | 给三个 Task 添加 schema guardrail | guardrails + tests | 非法输出反馈并限定重试 | 自愈与重试边界 |
| P03-10 | 建立 typed Flow state | Flow state model | 序列化/恢复测试 | Flow state |
| P03-11 | 实现 00–03 Flow 步骤 | flow slice + fake tools | 请求到 document manifest 可运行 | 编排与持久化 |
| P03-12 | 接入 sequential Crew 形成 04–05 步骤 | flow slice + fake LLM | packs 与 draft 工件落盘 | Crew 嵌入 Flow |
| P03-13 | 实现质量门禁 06 | quality service | 缺引用/改数字/缺章节均被拦截 | deterministic guardrails |
| P03-14 | 实现发布和 `RunManifest` 07 | manifest + tests | hash、版本、耗时和模型信息齐全 | 可复现性 |
| P03-15 | 做纯 fake 端到端测试 | one E2E fixture | 不联网完成全流程 | 测试替身、纵向切片 |

### Phase 3.5：受控反思与修订闭环（补充升级，位于 P03-15 后、P04-01 前）

> 说明：仅扩展 Phase 3，不修改 P03-01～P03-15 编号。核心是全确定性的
> 质量门禁 → 结构化质量问题 → 受控路由（发布/修订/补证/拒绝），
> 由 Flow + ReflectionController 控制，Agent 之间不互相调用。

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P03-16 ✅ | 定义结构化质量问题与修订请求 | QualityIssue、RevisionRequest、SupplementResearchRequest、枚举 | 序列化和边界测试 | 结构化错误模型 |
| P03-17 ✅ | 将质量门禁升级为结构化问题分类 | quality classifier | 正确区分发布、修订、补证和拒绝 | 确定性分类 |
| P03-18 ✅ | 实现 Writer 定向修订 Task | revision prompt/task | 只修复指定问题，不新增事实 | 定向修订 |
| P03-19 ✅ | 实现证据不足补充研究流程 | supplement request/task | 最多补证一次 | 一次性补证 |
| P03-20 ✅ | 实现受控反思路由 | ReflectionController + Flow routing | 无无限循环，路由和次数正确 | 有界反思 |
| P03-21 ✅ | 增加纯 fake 反思闭环 E2E | fixtures + E2E tests | 离线覆盖发布、修订、补证和拒绝 | 反思闭环 E2E |

## Phase 4：API、队列与本地运行

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P04-01 ✅ | FastAPI app、health、readiness | endpoints + tests | DB/Redis 状态分开报告 | liveness vs readiness |
| P04-02 ✅ | `POST /v1/research-jobs` | route/service/tests | 202 + job_id；无效请求 422 | async job API |
| P04-03 ✅ | `GET /v1/research-jobs/{id}` | route/tests | 状态、步骤、错误和耗时返回 | 查询 DTO |
| P04-04 ✅ | 工件清单与安全下载接口 | routes/tests | 只允许该 job 的已登记工件 | 路径穿越防护 |
| P04-05 ✅ | 客户端 Idempotency-Key | middleware/service/tests | 同 key 同请求复用，不同请求 409 | API 幂等 |
| P04-UI-01 ✅ | Streamlit 骨架与 typed API client | app 入口 + client + tests | fake HTTP API 下能调用 health/readiness | 前端即 HTTP 客户端 |
| P04-UI-02 ✅ | 创建投研任务页面 | 页面 + client/tests | 携带 Idempotency-Key 创建，展示 job_id | 幂等创建 UX |
| P04-UI-03 ✅ | 任务状态、步骤和错误轮询页面 | 轮询页面 + tests | 自动轮询至终态，展示错误与耗时 | 轮询状态机 |
| P04-UI-04 ✅ | 报告、质量结果、引用和工件页面 | 展示/下载页面 + tests | 展示报告/质量/引用/限制，安全下载工件 | 展示层只读 |
| P04-06 ✅ | 启动 Redis 与 Celery 最小 worker | queue config + smoke test | API 投递、worker 消费 fake task | broker/worker |
| P04-07 ✅ | Worker 调用 Flow | task adapter + tests | 状态从 pending 到终态 | 长任务边界 |
| P04-08 ✅ | 实现取消标志和安全点 | endpoint + flow check | pending/running 取消行为正确 | 协作式取消 |
| P04-09 ✅ | 编写 Dockerfile | multi-stage image | 非 root 运行、healthcheck 通过 | 容器最小权限 |
| P04-10 ✅ | 编写 Docker Compose | API/worker/Postgres/Redis/migrate | 一条命令启动；创建任务→Worker→终态 smoke 全过 | 本地编排 |
| P04-UI-05 ✅ | 将 Streamlit 加入 Docker Compose | compose 服务 + frontend | 前后端一条命令启动；UI health 200 | 前后端编排 |
| P04-UI-06 ✅ | 任务列表 API 与稳定分页 | `GET /v1/research-jobs` + DTO/Service/Store/tests | limit/status/cursor 过滤正确，created_at 倒序稳定，cursor 无重复遗漏，非法入参 422，空库 items=[]，production wiring 不返回 503 | cursor 分页、稳定排序 |
| P04-UI-07 ✅ | 最近任务/任务中心页面 | 任务中心页面 + 分页/筛选/查看详情/tests | 只调用列表 API，中文状态标签，分页与筛选，空态与错误重试，刷新后重新读取 | 列表 UX、分页状态 |
| P04-UI-08 ✅ | job_id 状态与 URL 持久化 | `frontend/state.py` + URL/session 恢复/tests | 创建写 session+query_params，初始化优先 URL，无 job_id 回退 session 与选择器，UUID 校验，非法不调 API | URL 作为任务上下文 |
| P04-UI-09 ✅ | 局部轮询与终态停止 | `st.fragment(run_every=...)` + 轮询决策/tests | 仅刷新状态/耗时区域，终态停止自动轮询，不重复创建，不生成新幂等键 | fragment 局部更新 |
| P04-UI-10 ✅ | 创建→详情→结果连贯导航 | 创建成功写 job_id → 详情（状态/耗时/步骤/取消/错误建议）→ 报告工件（空态）→ 返回任务中心 | 表单稳定幂等键，取消后刷新，failed/partial 展示可读建议，共享展示函数 | 用户路径连贯性 |
| P04-11 ✅ | 编写 CLI demo | `research run/status/artifacts` | CLI 通过 HTTP 创建/轮询/列工件；fake HTTP 测试过 | API client UX |

### Phase 4 UI：Streamlit 轻量操作界面（P04-UI 系列）

> 说明：P04-UI 系列是 Phase 4 的补充任务，**不修改现有 P04 编号**。
> Streamlit 只作为 FastAPI 的 HTTP 客户端，不直接访问数据库/Redis，也不直接调用
> CrewAI Flow。前端规则见 `docs/02-ARCHITECTURE.md §11`、需求见 `docs/01-PRD.md §14`。

前端边界（P04-UI 全系列通用）：

- 只调用 FastAPI；API 地址通过环境变量配置。
- 创建任务使用客户端 `Idempotency-Key`。
- 测试使用 fake HTTP API，不依赖真实数据库、Redis、Docker 或模型。
- 页面不负责业务判断。
- 不在 session state 保存密钥；不显示数据库连接字符串和内部文件路径。
- 不引入 React、Vue、Node.js；暂不实现登录、权限系统和复杂响应式设计。

#### 2.4 Phase 4 UI 实施时序

P04-UI 依赖对应后端接口已就绪，实施顺序必须为：

```
P04-01 → P04-02 → P04-03 → P04-04 → P04-05
      → P04-UI-01 → P04-UI-02 → P04-UI-03 → P04-UI-04
      → P04-06 → P04-07 → P04-08 → P04-09 → P04-10
      → P04-UI-05
      → P04-11
      → P04-UI-06 → P04-UI-07 → P04-UI-08 → P04-UI-09 → P04-UI-10
```

- `P04-UI-01` 依赖 `P04-01`（health/readiness 可用于 typed client 验收）。
- `P04-UI-02` 依赖 `P04-02`（创建任务 API）与 `P04-05`（Idempotency-Key）。
- `P04-UI-03` 依赖 `P04-03`（任务/步骤状态查询 API）。
- `P04-UI-04` 依赖 `P04-04`（工件清单与安全下载 API）。
- `P04-UI-05` 依赖 `P04-10`（Docker Compose）与 `P04-UI-01~04`（前端已可运行）。
- `P04-UI-06` 依赖 `P04-10A`（生产 wiring 与真实 SQL Store）与 `P04-03`（查询 DTO 基础）。
- `P04-UI-07` 依赖 `P04-UI-06`（任务列表 API）。
- `P04-UI-08` 依赖 `P04-UI-07`（最近任务选择）与 `P04-UI-02`（创建成功拿 job_id）。
- `P04-UI-09` 依赖 `P04-UI-03`（轮询服务）与 `P04-UI-08`（job_id 上下文）。
- `P04-UI-10` 依赖 `P04-UI-07/08/09`（连贯导航收口）。
- 单用户边界：当前为单用户本地部署，任务列表展示该实例的全部任务；暂无用户认证与多用户隔离，不应直接暴露到不可信公网。

## Phase 5：可靠性、监控与评估（补齐第 5 周）

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P05-01 ✅ | 实现通用 retry policy | Tenacity policy + tests | 只重试白名单错误，测试无真实 sleep | backoff/jitter |
| P05-02 ✅ | 尊重 `Retry-After` 并动态降速 | HTTP policy + tests | 429 fixture 行为可证明 | rate-limit cooperation |
| P05-03 ✅ | 实现步骤 lease 与 stale recovery | recovery job + tests | 模拟 worker 崩溃后可恢复 | at-least-once execution |
| P05-03A ✅ | Worker 启动自动 stale recovery 与恢复计数 | recovery bootstrap + tests | worker 启动自动扫描超租约步骤，恢复次数可观测 | at-least-once 启动语义 |
| P05-03B ✅ | Transactional Outbox 与 pending 投递恢复 | outbox_events 表 + relay + tests | Job 创建与事件同事务；dispatch 失败持久化重试；重启恢复；防重复投递 | transactional outbox |
| P05-04 ✅ | 实现输入 hash 和下游失效 | versioning service | prompt/schema 变化触发正确重算 | cache invalidation |
| P05-05 ✅ | 结构化日志与敏感字段脱敏 | structlog config + tests | key/header/cookie 不出现在日志 | observability security |
| P05-06 ✅ | 暴露 Prometheus 指标 | `/metrics` + tests | 无 job_id 高基数 label | metrics design |
| P05-07 ✅ | 添加 OpenTelemetry trace | tracing config | API→worker→step trace 可关联 | distributed tracing |
| P05-08 ✅ | 写 Grafana 本地最小 dashboard | dashboard JSON | 成功率、P95、重试、错误可见 | RED/USE 指标 |
| P05-09 ✅ | 故障注入：timeout/429/5xx | tests | 重试次数和最终状态符合矩阵 | resilience testing |
| P05-10 ✅ | 故障注入：坏 PDF/非法 LLM JSON | tests | 降级和 guardrail 上限正确 | bounded recovery |
| P05-11 ✅ | 故障注入：数据库/本地工件写入失败 | tests | 不能误报成功，无半成品发布 | commit semantics |
| P05-12 ✅ | 录制一家公司 SEC 契约 fixture | fixture + sanitizer | CI 离线可回放，无敏感字段 | record/replay |
| P05-12A ✅ | FLOW_MODE=fake/live 与生产 Flow wiring | wiring + contract tests | fake/live 模式切换，普通测试用 fake | production wiring |
| P05-12B ✅ | 真实 LLM、Agent 工具和 LiveResearchFlowRunner 生产组装 | live builder + wiring | Agent 接受统一 LLM 协议；真实工具注入；live 缺配置 fail-fast | production wiring |
| P05-13 ✅ | 真实服务 E2E smoke（fast live run 通过：published，240s，含真实 SEC/Serper/LLM 证据） | opt-in test（RUN_LIVE_E2E=1） | 固定公司生成带引用报告 | external integration |
| P05-14 ✅ | 建立 20 公司 × 5 场景基准定义 | `evals/dataset.*` | 场景、行业和截止日固定 | evaluation design |
| P05-15 ✅ | 实现 benchmark runner 和汇总 | runner + report template | fake/fixture 跑 100 条；输出成功率、P50/P95、失败分布 | SLO measurement |

## Phase 5.5：性能优化（精简版，P05-13 之前的性能补齐）

> 说明：只做四项性能优化，不改前端、不做优化前后 benchmark；P05-13 已于 fast live run
> 真实通过（published，约 240s），随后标 ✅。

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P05.5-1 ✅ | 记录 Research/Analysis/Writer 耗时、工具耗时/调用次数、LLM token usage，写入 manifest.performance | `infrastructure/performance.py` + `flows/manifest.py` performance 字段 | 三 Agent 耗时、工具耗时/次数、token usage（不可得为 null）汇总进 07_manifest；不记录密钥/Authorization/完整 Prompt | performance telemetry |
| P05.5-2 ✅ | 新增 RESEARCH_PROFILE=fast/deep（默认 deep），预算集中到 ResearchProfile | `settings.py` ResearchProfile + Agent 预算注入 | fast=3/2/1 iter、max_retry_limit=1、timeout=180s、max_rpm=60；deep 完整能力；参数集中在统一配置对象 | centralized budget |
| P05.5-3 ✅ | 公司身份确认后并行 SEC/Serper/缓存查询 + 相同工具名+规范化参数单 Job 只执行一次 | `tool_cache.py` + prefetch + real_tools 缓存包装 | 解析后 ThreadPoolExecutor 并行预取并预热缓存；同参数只执行一次；保留限流/Retry-After/as_of_date | parallel I/O + memoization |
| P05.5-4 ✅ | 三角色模型配置完善：LLM_MODEL_RESEARCH/ANALYSIS/WRITER 空名 fail-fast | llm_factory 校验 + tests | 空/空白模型名抛 ValidationError；不硬编码供应商/模型名；不降级 fake | fail-fast config |

## Phase 6：报告、本地部署与作品集收尾

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P06-01 ✅ | 用 Jinja2 固化 Markdown 模板 | template + golden test | 章节和引用稳定 | 模板化生成 |
| P06-02 ✅ | 增加 PDF 渲染 | PDF adapter + visual check | 中文字体、Markdown 样式、链接、分页正常（4 页样例已逐页检查） | 文档渲染 |
| P06-03 ✅ | 完善本地工件生命周期 | cleanup service + tests | 临时文件、过期工件和基准工件按策略处理 | retention、atomic files |
| P06-04 ✅ | 完善本地密钥与生产配置 | settings profiles + docs | 密钥不进镜像、Git、日志或 manifest | secrets、configuration |
| P06-05 ✅ | 完成本地 OpenTelemetry 链路查看 | collector/exporter config | FastAPI→Worker→Flow→Tool trace 可关联 | trace、span、context |
| P06-05A ✅ | 补齐真实财务事实数据流 | SEC Company Facts prefetch + Analysis input | 事实按 filed/as_of 截断、可追溯且不再默认为空 | point-in-time data、grounding |
| P06-06 ✅ | 建立完整本地 Docker Compose profile | compose + config | API、Worker、PostgreSQL、Redis、Prometheus、Grafana 和 trace 组件可启动 | local operations |
| P06-06A ✅ | 每任务 fast/deep 研究档位 | ResearchProfileMode + 0007 迁移 + 前端档位单选/徽章 | 前端显式传 research_profile；旧请求默认 deep 兼容；列表/详情显示档位；迁移真实 PostgreSQL upgrade→downgrade→upgrade 通过 | per-job budget、UI/domain 默认分离 |
| P06-06B ✅ | 实时步骤状态与最小前端进度 | ProgressSink 协议 + SqlProgressSink + Flow/Worker 步骤标记 + 前端阶段中文映射 | Worker 幂等创建 00-07；合法状态转换；失败不留虚假 running；终态清空 current_step；前端当前阶段；旧任务 steps=[] 兼容 | 真实步骤边界、短事务、幂等创建 |
| P06-06C ✅ | 接通 Prometheus 业务指标与 Jaeger 真实 trace 数据链 | OTLP endpoint 规范化 + 业务指标事件 + Worker 多进程指标端点 + Prometheus/Grafana 修复 | 离线 48+48 passed、Ruff/mypy 全绿；Docker fake 验收：Prometheus 两 target up、research_jobs_total 与 Histogram bucket/sum/count 非空、Jaeger 出现 api/worker 服务且 trace 跨 Outbox/Celery 关联、无 OTLP 404、无真实外部调用 | 多进程 Registry、OTLP 路径规范化、事件语义防重复计数 |
| P06-07 ✅ | 执行本地部署 smoke test | scripts/docs（docs/17-P06-07-VALIDATION.md） | health、任务执行、报告下载、指标和链路查询通过（真实 live 任务 succeeded、8 工件齐全、Prometheus 三状态计数、Jaeger 双服务 span 落库） | deployment verification |
| P06-08 ✅ | 实现 PostgreSQL 与工件备份恢复演练 | scripts + runbook | 从备份恢复一个完整 job、步骤和报告 | RPO/RTO、recovery |
| P06-09 ✅ | 建 GitHub Actions CI | workflow（.github/workflows/ci.yml） | lint、type、unit、integration 通过（Ruff / mypy src / 离线 pytest SKIP_DB_TESTS=1 893 passed / migration+DB 集成用真实 PostgreSQL） | CI quality gate |
| P06-09A ✅ | FinancialAnalysisPack 结果完整性状态 | domain/models（AnalysisCompleteness + schema_version v2 + unavailable_reason）+ 提示词 v2 + quality 兜底 | complete/partial/unavailable 三态合法构造与矛盾被拒；v1 旧工件兼容读取；partial/unavailable 不崩溃（tests/test_analysis_completeness.py 18 用例，回归 85 passed） | pack 语义、跨字段校验 |
| P06-09B ✅ | 统一 PackBoundary | agents/pack_parsing（PackBoundary）+ flow_wiring 接入 | 提取→来源分类→分层校验→限 1 次修复；Action Input/工具参数拒绝；错误脱敏；修复失败返原错误（tests/test_pack_boundary.py 18 用例，回归 71 passed，ruff/mypy 通过） | 边界解析、有限修复 |
| P06-09C ✅ | Prometheus + Grafana + Jaeger 可观测性增强 | metrics.py/metrics_events.py 全量新指标 + api/worker 接线 + flow_wiring agent 子 span + execution 结构化日志 + 7 组 Grafana dashboard + observability smoke | HTTP RED / Job+profile / stale_recovery / failure / agent / pack / tool-cache / llm 全指标接线；agent span 挂 flow.run 子 span；job_flow_succeeded/failed 结构化日志带 trace_id；dashboard 7 Row 全部 histogram_quantile 且无高基数 label；隔离 fake 栈 smoke：20 任务终态、Prometheus/Jaeger/Grafana 全绿、worker 无真实外部调用（docs/18-P06-09C-VALIDATION.md） | 多进程 Registry 时序、label 白名单、trace-log 关联 |
| P06-10A ✅ | 建立 P06-10 基准框架并完成 10 次 fake 校准 | scripts/run_phase6_benchmark.py + deploy/compose.benchmark.yml + tests/test_phase6_benchmark.py + evals/runs/<run_id>/（docs/19-P06-10A-BENCHMARK.md） | 端到端 HTTP 基准（API→Worker→DB→Flow→工件校验）；10 条成功判定；主任务/控制任务分母隔离；fast/deep 分组；P50/P90/P95/P99 按 jobs.jsonl 计算；worker 无真实外部调用（0 条 sec.gov/serper.dev/chat）；校准 10/10 = 100%（docs/19）｜Ruff/mypy 全绿｜14 测试通过 | 端到端基准归因、分母隔离、live fail-fast |
| P06-10 ✅ | 执行 100 次基准并归因失败 | docs/20-P06-10B-BENCHMARK.md + evals/runs/<run_id>/（config/jobs.jsonl/summary/failures/metrics_snapshot_*/report） | 100/100 = 100%（≥96 通过）；10 条成功判定逐项满足；fast/deep 各 50/50；P50/P90/P95/P99 从 jobs.jsonl；Prometheus 19091 before/after 真实快照（非空占位）；worker 无真实外部调用（0）；控制任务不进入分母 ｜ Ruff/mypy 全绿｜18 测试通过（含 4 个 Prometheus 快照测试） | evidence-based resume、Prometheus 旁证接入、P50/P99 分位数 |
| P06-11A ✅ | LLM Token、延迟与调用链可观测性准确性收口 | infrastructure/observability/llm_call_observer.py（CrewAI LLM 事件总线观测器）+ flow_wiring 集成（删除三倍计数）+ Jaeger llm.request span + Grafana LLM Row（Token 1h/24h、成功/失败、P50/P95/P99、usage missing、provider/model/role 筛选）+ tests/test_llm_observability.py（20 用例）+ tests/test_grafana_dashboard.py（PromQL 解析） | 每次真实 LLM 调用只计数一次（不三倍）；三角色数据各自独立；成功/失败均记录次数与耗时；token 只来自真实响应 usage（缺失仅记 llm_usage_missing_total，不伪造 0）；llm.request span 属性仅 provider/model/role/status + duration/tokens，无 prompt/response/key；Grafana PromQL 用 rate/increase 且无敏感 label ｜ Ruff/mypy 全绿｜20 新测试 + 回归 101 passed （test_llm_observability 20 + test_flow_wiring/test_metrics 55 + observability/tracing 46） | CrewAI 官方 LLM 事件总线、event-base usage 正式字段、Started→Completed 本地耗时实测
| P06-11B ✅ | 修复 DeepSeek 与 CrewAI 结构化输出不兼容 | agents/llm_factory.py（StructuredOutputMode + structured_output_mode 按 LLM_VENDOR 集中决策）+ agents/analysis_task.py + agents/writer_task.py（JSON 文本 + 本地校验路径，DeepSeek/generic 不绑 output_pydantic，提示词明确只输出 JSON object）+ domain/errors.py（STRUCTURED_OUTPUT_UNSUPPORTED）+ application/failure_classifier.py（response_format 拒绝优先分类）+ infrastructure/flow_wiring.py（kickoff 异常优先结构化输出根因 + 前置迭代信息日志）+ tests/test_p06_11b_deepseek_structured_output.py（27 用例） | DeepSeek Research/Analysis/Writer 均不配置原生 output_pydantic/output_json（避免触发 beta.chat.completions.parse）；Qwen 保留 output_pydantic 原路径；DeepSeek 合法 JSON 文本经本地 PackBoundary 解析为 ResearchPack/FinancialAnalysisPack/ReportDraft；缺 version/title/markdown 等必填字段仍失败；工具参数/ArtifactReader 参数不被当作 Pack；模拟 response_format 400 分类为 STRUCTURED_OUTPUT_UNSUPPORTED（不可重试，不归 INTERNAL_BUG/NETWORK_TRANSIENT/ITERATION_LIMIT）；API Key 不出现在 repr/异常/错误消息；Qwen/DeepSeek 三角色模型配置仍分别生效 ｜ Ruff/mypy 全绿｜27 新测试 + 回归 143 passed（pack boundary/contracts、research/analysis/writer task、llm_factory、crew_factory、flow_wiring、final_report_artifacts） | CrewAI task.py:768 中 output_json 与 output_pydantic 进入同一 convert_to_model → Converter 调 llm.call(response_model=...) 触发 OpenAI beta parse；DeepSeek 普通 Chat Completion 不支持 json_schema response_format（HTTP 400）
| P06-11C ✅ | 修复 DeepSeek 普通 JSON 路径的嵌套 FinancialFact 契约丢失 | domain/models.py（AnalysisSelectionDraft 选择草稿：只含 selected_fact_refs/metric_results/notes/limitations/completeness）+ application/analysis_assembler.py（AnalysisPackAssembler：确定性组装器 + build_fact_ref 稳定 hash + parse_fact_records）+ real_tools._serialize_facts（每条事实追加 fact_ref）+ agents/analysis_task.py（DeepSeek/generic 提示词要求只输出 Draft，不抄写 FinancialFact）+ agents/crew_factory.py（Writer loader 现场把 Draft 组装为 FinancialAnalysisPack）+ infrastructure/flow_wiring.py（_parse_prefetched_facts 还原原始事实 + _extract_analysis_pack 检测 Draft 并组装）+ domain/errors.py（FACT_REFERENCE_UNRESOLVED/FACT_REFERENCE_AMBIGUOUS/FACT_PROVENANCE_MISMATCH 不可重试）+ prompts/analysis_prompt_v2.md（只输出 Draft 契约 + 禁止抄写 SEC 数值）+ tests/test_p06_11c_analysis_assembler.py（26 用例） | DeepSeek/generic 路径：普通 JSON 文本 → AnalysisSelectionDraft → AnalysisPackAssembler → FinancialAnalysisPack（company_id/source_id/value/unit/period 全部来自原始预取事实，LLM 只能选择 fact_ref，无法改写数值）；未解析 ref → FACT_REFERENCE_UNRESOLVED、多重匹配 → FACT_REFERENCE_AMBIGUOUS、公司身份不一致 → FACT_PROVENANCE_MISMATCH（均非网络错误不可重试）；Writer 的 ArtifactReader 读到的必是组装后 FinancialAnalysisPack；Qwen 原 NATIVE_PYDANTIC 路径不受影响；FinancialFact.company_id 保持必填 ｜ Ruff/mypy 全绿｜26 新测试 + 回归 219 passed（p06-11b/pack_boundary/pack_contracts/analysis_task/analysis_completeness/flow_wiring/writer_task/research_crew/final_report_artifacts/real_tools/models/prompts） | 根因：DeepSeek JSON 文本路径下模型没有获得完整嵌套 FinancialFact 契约 → facts[0] 丢 company_id；P06-11B 只解决 400 未覆盖嵌套契约；真实预取数据含 company_id，是 LLM 重抄 JSON 时丢字段，非 SEC 数据缺失
| P06-11D ✅ | 修复 DeepSeek Writer 普通文本无法转换 ReportDraft | application/report_draft_assembler.py（ReportDraftAssembler：确定性组装器 + build_report_title + citation key 提取 = 复用 AnalysisPackAssembler.build_fact_ref）+ agents/writer_task.py（DeepSeek/generic Writer 只输出 Markdown 正文，不再要求 JSON 包装）+ infrastructure/flow_wiring.py（统一 _extract_report_draft：报告正文 → ReportDraftAssembler → ReportDraft）+ domain/errors.py（REPORT_INVALID/REPORT_TRUNCATED 不可重试）+ tests/test_p06_11d_report_draft_assembler.py（26 用例） | DeepSeek/generic Writer 只输出普通 Markdown 报告正文 → ReportDraftAssembler 确定性生成 version（代码固定）/title（从 ResearchPack.company_identity.legal_name/ticker + as_of_date）/markdown（原文保留，引号/换行/表格无需 JSON 转义）/citation_keys（只从可信来源提取正文中实际出现的键，模型无法伪造）；空文本/过短说明/Tool Action/Action Input/“无法生成报告”等拒绝文本 → REPORT_INVALID；明显截断（finish_reason=length 或末尾未完标志）→ REPORT_TRUNCATED；缺任何必需章节 → REPORT_INVALID；完整章节缺失由现有 Quality Gate REVISE；Qwen 继续绑定 output_pydantic=ReportDraft 不受影响；一次 fake 全链产生合法 ReportDraft ｜ Ruff/mypy 全绿｜26 新测试 + 回归 111+70 passed | 根因：DeepSeek 普通 Chat Completion 没有服务端结构化约束（response_format 仅提示词，实测返回 12 token 左右简短自然语言说明）→ PackBoundary 拒绝（NOT_A_PACK "输出是普通自然语言，不是结构化 Pack"）；普通 Markdown 更适合长报告（无需 JSON 转义，模型专注正文）；确定性字段来源：version=代码固定、title=可信公司身份 + as_of_date、citation_keys=可信集合 ∩ 正文实际出现
| P06-11E ✅ | 实现 DeepSeek 原生 JSON Finalizer，再拆阶段执行 | application/structured_finalizer.py（StructuredFinalizer 供应商无关端口）+ infrastructure/finalizers/deepseek_json_object_finalizer.py（DeepSeekJsonObjectFinalizer：纯 chat.completions.create + response_format=json_object；tools=[]、thinking=false、检查 finish_reason、空 content/length 稳定失败、最多一次格式修复、第二次失败即终止）+ application/boundary_canonicalizer.py（仅语义等价规范化：""→None、strip、不修改数字/枚举、不补 company_id/source_id；unavailable+空原因仍失败）+ domain/models.py（ResearchSelectionDraft）+ application/research_assembler.py（ResearchPackAssembler：选择草稿 → 可信 SEC 申报记录确定性组装 Source）+ infrastructure/flow_wiring.py（_RunContext 每次 run 独立：finalization_count/prefetch_result/analysis_facts 限定当前 Job；生产路径 Research→Analysis→Writer 三阶段短路，前序成功才执行下一步）+ tests/test_p06_11e_deepseek_json_finalizer.py（22 用例） | 普通 Agent 工具循环 → 独立 JSON Finalizer → BoundaryCanonicalizer → Pydantic → 确定性 PackAssembler；DeepSeek/generic Research/Analysis 用 Finalizer + SelectionDraft + Assembler，Writer 保持 Markdown→ReportDraftAssembler；Qwen 保持 NATIVE_PYDANTIC 回归；服务端 response_format=json_object 强制 JSON 对象、绝不触发 beta.chat.completions.parse/json_schema；不全局给所有 Agent 请求添加 json_object（只在 Finalizer 请求携带）；finish_reason=length / 空 content / 第二次修复失败均稳定终止（不重跑整个 Agent）；两个连续 Job 状态隔离 ｜ Ruff/mypy 全绿｜22 新测试 + 回归 tests/test_flow_wiring.py 25 passed | DeepSeek 服务端无内置结构化输出（response_format=json_schema 不支持）；需独立 Finalizer 用 json_object 强制 JSON + 本地 Pydantic 校验；_finalize_used 等实例状态必须限定在每次 run 的 _RunContext（跨 Job 泄漏是真实缺陷）；阶段短路让失败不再执行后续 Agent（错误归因清晰、节省费用）
| P06-11F ✅ | Writer 引用注册表 + 质量门禁准确性 + 有界修订 | application/citation_registry.py（CitationRegistry/CitationRegistryEntry：src 复用 build_source_citation_key、fr 复用 build_fact_ref，唯一生成来源；WriterContextReader 完整交付；Assembler 复用同一 registry）+ application/empty_value_policy.py（EmptyPolicy REQUIRED/CONDITIONALLY_OPTIONAL/OPTIONAL + check_analysis_completeness，集中式空值策略）+ application/deterministic_revision.py（纯函数有界修订：missing_citation_keys 包装合法 key / invalid_citation_key 删除伪造 key / forbidden_advice 删除建议行；不重新执行 Research/Analysis/外部工具）+ flows/quality_classifier.py（禁止项从裸子串改为上下文正则：真实建议拒绝、"不构成买入、卖出或目标价建议"不误判；citation_keys 不无条件放宽，外部事实空引用 REVISE；非法 key CRITICAL）+ flows/state.py（citation_registry + revision_attempted/succeeded）+ flow_wiring/_apply_bounded_revision（有界一次修订 + 重新组装 + 重新门禁 + revision 指标）+ metrics（revision_total）+ writer_prompt_v2（引用格式 [src_<hash>]/[fr_<hash>]、只能复制注册表 key、保留非投资建议声明）+ tests/test_p06_11f（22 用例） | P06-11E 真实报告 citation_keys 为空根因：WriterContextReader 未把最终 src/fr key 交给 Writer（key 是输出后由 Assembler 计算）；修复为 Writer 前生成注册表并完整交付；禁止项误判确认存在（免责声明被纯子串命中）；修订最多 1 次，第二次失败保持 rejected 并保留原稿/修订稿/质量报告；合法 partial/unavailable → PUBLISH_PARTIAL；complete 缺核心事实 → REJECT；空字符串规范化只作用于 OPTIONAL 字段 ｜ Ruff/mypy 全绿｜22 新测试 + 回归 323 passed（quality_classifier/flow_wiring/flow_quality/metrics/p06-11b~e/writer/revision/e2e_fake/final_report_artifacts/pack） | 学习点：唯一生成来源原则（同一 hash 算法不重写第二套）；句子分隔符回溯确定免责声明语境（避免"本句建议、后句免责"误判）；确定性修订必须满足"删除不新增、注册表为空不伪造"
| P06-11I ✅ | 重构 Writer 为无工具单轮写作流程 | application/writer_context_builder.py（确定性紧凑上下文，保留身份/citation keys 确定性裁剪）+ application/writer_direct_dispatch.py（WriterDirectDispatch 端口 + WriterDispatchResult + WriterDispatchError）+ infrastructure/direct_writer_dispatch.py（DeepSeek/generic 一次无工具 chat.completions.create，只读 response.content）+ flow_wiring（_exec_writer_stage_direct：Python 加载 Pack → 紧凑上下文 → 一次调用 → ReportDraftAssembler → 质量门禁；Qwen NATIVE_PYDANTIC 原路径保留）+ metrics/metrics_events（writer_direct_requests_total{status}/duration_seconds/output_chars/retry_total{reason}）+ tests/test_p06_11i_writer_direct.py（19 用例，覆盖 17 项验收重点） | DeepSeek/generic Writer 不再创建 WriterContextReader Agent/Crew 工具循环（max_iter 内反复调用消耗 token、final 过短的根因）；一次无工具调用直接输出 Markdown；空/length/过短/缺章节最多一次 Writer-only 重试（复用同一份上下文）；第二次失败稳定 REPORT_INVALID/REPORT_TRUNCATED；请求绝不包含 tools/tool_choice/beta.parse；context_build/direct_llm/assemble Jaeger span 只记长度与数量；Ruff 全绿；mypy 130 源文件全绿；19 新测试 + 回归 207 passed（flow_wiring/metrics/p06-11b~h/observability） | 无工具单轮 Writer、确定性紧凑上下文、有限重试边界 |
| P06-11K-1 ✅ | 诊断配置 + 事件模型 + 递归脱敏 + Job-local 有界缓冲 | settings.py/.env.example（DIAGNOSTIC_CAPTURE_MODE=off 默认 + MAX_EVENT_BYTES/MAX_BUNDLE_BYTES/MAX_EVENTS/RETENTION_DAYS + production all_payload fail-fast）+ application/diagnostics/{models,redaction,sink,__init__}.py（DiagnosticEvent/ValidationErrorEntry/DiagnosticCapturePolicy；递归脱敏 reason/CoT 永不保存；DiagnosticCaptureSink Protocol + BoundedDiagnosticBuffer Ring Buffer）+ tests/test_p06_11k1_diagnostics_buffer.py（23 用例） | 默认 off；metadata 不存 payload；单事件/总 Bundle/Ring Buffer 三容量受限且截断置 truncated；finalize 幂等；两个并发 Job 不串事件；API Key/Authorization/Cookie 递归脱敏；reasoning_content 永不保存（脱敏器 + Buffer 纵深防御双保险）；测试 3/4/5/6/9/10/11/12/13 对应验收全绿 ｜ Ruff 全绿 + K-1 模块 mypy 无问题（全 src mypy 的 research_flow.py:89 错误为既有、与 K-1 无关） | 事件模型、递归脱敏、容量受限 Ring Buffer、幂等收口 |
| P06-11K-2 ✅ | 诊断包持久化 + finalize + 生命周期清理 | application/diagnostics/persistence.py（DiagnosticManifest + DiagnosticBundleFiles + build_bundle_files 落盘语义）+ infrastructure/diagnostics_bundle_store.py（resolve_diagnostics_dir UUID 防穿越 + DiagnosticsBundleStore 原子写 mkstemp/fsync/os.replace + manifest 最后写 + write_if_absent 幂等 + DiagnosticsCleanupService 按 expires_at 清理）+ tests/test_p06_11k2_diagnostics_persistence.py（11 用例） | 失败任务能保留失败前所有阶段；成功任务 failure_payload 不落 Payload；落盘失败不覆盖原始业务异常（调用方模式验证）；finalize_failure 重复调用落盘幂等（write_if_absent 返回 None）；保留天数清理过诊断包；原子写无 .tmp-/*.part 残留；job_id 非 UUID 拒绝（防路径穿越）；不回改变任务恢复语义 ｜ Ruff/mypy 全绿（6 源文件无问题）+ K-1/K-2 共 34 passed | 失败落盘、原子写、retention |
| P06-11K-3 ✅ | Research/Analysis/Writer/Quality Gate 核心阶段交接接线 | application/diagnostics/capture.py（DiagnosticCapture：先脱敏再入缓冲封装）+ flow_wiring（run() try/except 收口 + _finalize_diagnostics_failure/success + _persist_diagnostics_bundle 落盘；research_inputs/ResearchPack/analysis_inputs/FinancialAnalysisPack/writer_context/writer_response_*/ReportDraft/quality_report/revision_result 捕获点；build_flow_runner diagnostics_factory 透传）+ tests/test_p06_11k3_capture_wiring.py（5 用例） | Pack 上下游交接内容可追踪（capture 先脱敏后入 Buffer 验证 api_key→***）；Schema 错误转换预留在 K-3 事件模型（ValidationErrorEntry，K-3 版本接 Pydantic errors 转换留给 K-4 精确化）；fake 注入 Research/Analysis/Writer 三类 kickoff 失败均生成诊断包（manifest.json+failure.json）；诊断收口写入失败绝不掩盖业务异常（外 try 内 try 隔离）｜ Ruff/mypy 全绿 + 5 新测试通过 | 阶段边界接线、先脱敏再入缓冲、收口幂等 |
| P06-11K-4 ✅ | LLM/CrewAI 工具/SEC/Serper/Calculator 调用信息 + trace 关联 | application/diagnostics/tool_summaries.py（工具参数白名单摘要 + SEC/Serper 结果结构化摘要（accession/locator/标题/数量）+ LLM 三态摘要 content/tool_calls/empty）+ real_tools（_cached_execute/_capture_tool_call 工具摘要捕获，cache 命中不重复捕获结果）+ llm_full_observer（LLM 三态摘要入诊断包 + Jaeger Span 只加 diagnostic.event_id/available/payload_kind/input_size/output_size/validation_error_count 白名单属性）+ api/app.py（GET /v1/research-jobs/{job_id}/diagnostics 下载脱敏诊断包 tar.gz，UUID 防路径穿越，不存在 404 明确提示）+ frontend（render_failed_diagnostics + build_diagnostics_event_rows 最近 10 条事件 + download_diagnostics_bundle；2_任务状态.py failed/partial 展示错误阶段/错误码/下载按钮/本地调试提示）+ tests/test_p06_11k4_api_download.py（5）/ frontend_diagnostics.py（6）/ obs_payload_redaction.py（10） | LLM content/tool_calls/empty 三态区分入诊断包；工具参数白名单摘要（api_key/authorization/cookie/URL 签名不进摘要）+ SEC/Serper 结果只含 accession/locator/标题/数量/有界预览；Jaeger/Prometheus 不包含完整 Payload（Span 属性白名单验证 + metrics 文本无 payload）；诊断包下载防路径穿越（非 UUID 422、../ 绝对路径 4xx 绝不 200、不存在 404 不 500）；前端失败详情页显示 failure_stage/error_code/最近 10 条事件（白名单列）+「下载脱敏诊断包」+「仅供本地调试」提示；每条事件 trace_id/span_id 已由 K-1 capture 透传 ｜ 21 新测试 passed + K-1/K-2/K-3/J 观测 54 回归 passed + 工具 span 4 passed；Ruff 全绿；mypy 受检 7 源文件无问题（research_flow.py:89 detach 为既有错误、与 K-4 无关） | 工具/SEC/Serper/LLM 脱敏摘要、Jaeger 低基数属性、安全下载、前端诊断入口 |
| P06-11K-5 ✅ | 生产接线补齐 + fake Docker 故障注入 + 文档收尾 | worker.py `_build_live_component_factory`/`_build_live_components`/`_PerJobFlowRunner` 补齐 `diagnostics_provider`/`diagnostics_factory`/`set_job_id` 生产接线（K-4 遗留：工具摘要捕获与 FlowRunner 诊断工厂贯通）；flow_wiring `_diag_trace_ids`/`_diag_capture` 统一注入当前 OTel trace_id/span_id（所有诊断事件与 Jaeger 链路同 trace）；real_tools `_capture_tool_call` 与 llm_full_observer `_capture_llm_summary` 同样注入 trace_id；tracing.py 新增公共 `current_trace_ids()`；tests/test_p06_11k5_fake_docker_injection.py（7 用例：工具摘要 request/response、cache 命中去重、预算耗尽只打参数、build_research_tools 签名、worker 注入点静态断言、fake Writer 失败诊断包脱敏+trace 一致、阶段顺序+ValidationError 落盘）+ docs/05、09、12 更新 | 生产路径工具摘要捕获全链路生效（provider 与 diagnostics_factory 产出同一 Job-local capture）；fake Writer Schema 失败 → LiveFlowExecutionError → 诊断包落盘（manifest/stage_payloads/validation_errors/failure）；阶段顺序可见 research_inputs→ResearchPack→analysis_inputs→FinancialAnalysisPack→writer_context→writer_response_content；api_key/authorization/cookie/reasoning_content 绝不在包内（脱敏 ***）；span 内诊断事件 trace_id 与 flow.run 一致；无真实 DeepSeek/SEC/Serper 调用（隔离 fake 栈）；完整相关回归全绿 ｜ Ruff 全绿（W292 已修复）；mypy 受检 6 文件无问题（research_flow.py:89 detach 为既有错误、与 K-5 无关）；7 新测试 + K-1~K-4/J/工具 span 回归 69+7 passed | 生产接线贯通（tools provider 与 diagnostics_factory 共享 state）、trace_id 一致（current_trace_ids 统一读取）、fake 故障注入纵向验收 |
| P06-11 | 做 10 家公司效率对照实验 | efficiency report | XX% 有原始时间记录 | ROI measurement |
| P06-12 | 完善 README 演示、架构图和限制 | portfolio README | 新用户可按步骤复现 | 技术表达 |
| P06-13 | 编写事故复盘与恢复演示 | postmortem/runbook | 至少一个故障注入→发现→恢复闭环 | production thinking |
| P06-14 | 生成简历项目描述 v1 | `docs/RESUME-BULLETS.md` | 只使用已测量数字 | 量化表达 |

## 3. 建议里程碑

- M1（P00 + P01）：骨架、领域模型和数据库可用。
- M2（P02）：所有确定性工具能在 fixture 上独立运行。
- M3（P03）：fake LLM 的三 Agent 全链路通过。
- M4（P04）：本地 API + Worker + Compose 可演示。
- M5（P05）：可靠性与评估闭环建立，开始真实 E2E。
- M6（P06）：本地完整部署、100 次基准和作品集材料完成。

## 4. 学习复盘节奏

每完成 3–5 个任务，单独做一次不改代码的复盘：画一遍当前数据流、解释一次失败如何恢复、列出一个仍不理解的问题。遇到以下情况立即回到 Plan 模式：

- 需要跨 5 个以上文件或修改数据库 schema。
- Cline 提议替换既定框架或扩大 MVP。
- 测试连续失败且根因不清楚。
- 实现与 PRD/ADR 冲突。
- 你无法用自己的话解释上一个任务。
