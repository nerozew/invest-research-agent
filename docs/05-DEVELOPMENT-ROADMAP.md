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
| P05-12B | 真实 LLM、Agent 工具和 LiveResearchFlowRunner 生产组装 | live builder + wiring | Agent 接受统一 LLM 协议；真实工具注入；live 缺配置 fail-fast | production wiring |
| P05-13 | 真实服务 E2E smoke | opt-in test | 固定公司生成带引用报告 | external integration |
| P05-14 | 建立 20 公司 × 5 场景基准定义 | `evals/dataset.*` | 场景、行业和截止日固定 | evaluation design |
| P05-15 | 实现 benchmark runner 和汇总 | runner + report template | 输出成功率、P50/P95、失败分布 | SLO measurement |

## Phase 6：报告、本地部署与作品集收尾

| ID | 小任务 | 产物 | 验收 | 学习点 |
|---|---|---|---|---|
| P06-01 | 用 Jinja2 固化 Markdown 模板 | template + golden test | 章节和引用稳定 | 模板化生成 |
| P06-02 | 增加 PDF 渲染 | PDF adapter + visual check | 中文字体、链接、分页正常 | 文档渲染 |
| P06-03 | 完善本地工件生命周期 | cleanup service + tests | 临时文件、过期工件和基准工件按策略处理 | retention、atomic files |
| P06-04 | 完善本地密钥与生产配置 | settings profiles + docs | 密钥不进镜像、Git、日志或 manifest | secrets、configuration |
| P06-05 | 完成本地 OpenTelemetry 链路查看 | collector/exporter config | FastAPI→Worker→Flow→Tool trace 可关联 | trace、span、context |
| P06-06 | 建立完整本地 Docker Compose profile | compose + config | API、Worker、PostgreSQL、Redis、Prometheus、Grafana 和 trace 组件可启动 | local operations |
| P06-07 | 执行本地部署 smoke test | scripts/docs | health、任务执行、报告下载、指标和链路查询通过 | deployment verification |
| P06-08 | 实现 PostgreSQL 与工件备份恢复演练 | scripts + runbook | 从备份恢复一个完整 job、步骤和报告 | RPO/RTO、recovery |
| P06-09 | 建 GitHub Actions CI | workflow | lint、type、unit、integration 通过 | CI quality gate |
| P06-10 | 执行 100 次基准并归因失败 | versioned eval report | 成功率结论有原始 run 支撑 | evidence-based resume |
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
