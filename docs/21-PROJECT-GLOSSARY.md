# 自动化投研系统术语表

> 本文用于解释本项目中常见英文技术术语。翻译以实际工程含义为准，不采用脱离语境的字面翻译。
>
> 最重要的理解原则：同一个英文单词在不同技术场景中可能表示完全不同的东西，例如 Model、Task、Context、Repository。

## 1. 项目整体关系

```text
用户请求 Request
  → 投研任务 Job
  → 执行步骤 Steps
  → Agent + Tools
  → 结构化数据包 Packs
  → 报告草稿 ReportDraft
  → 最终产物 Artifacts
  → 运行清单 Manifest
```

可观测性数据走另一条链路：

```text
应用产生 Metrics → Prometheus 保存 → Grafana 展示
应用产生 Traces  → OTel Collector  → Jaeger 展示
应用产生 Logs    → Docker/终端      → 开发人员排错
```

## 2. Artifact：产物 / 工件

Artifact 在软件工程中表示“某个过程生成并保存下来的结果”。本项目中最自然的中文是“运行产物”或“工件”。

一次投研任务可能产生：

```text
artifacts/<job_id>/
├── 00_request.json
├── 02_research_pack.json
├── 04_financial_analysis_pack.json
├── 05_report_draft.json
├── 06_quality_report.json
├── 07_manifest.json
├── 08_report.md
└── 09_report.pdf
```

中间产物主要供程序、下游 Agent 和开发人员使用：

- `02_research_pack.json`：研究数据包；
- `04_financial_analysis_pack.json`：财务分析数据包；
- `05_report_draft.json`：报告草稿结构；
- `06_quality_report.json`：质量门禁结果；
- `07_manifest.json`：本次运行的产物清单与元数据。

最终产物主要供用户使用：

- `08_report.md`：Markdown 报告；
- `09_report.pdf`：PDF 报告。

保存 Artifact 的作用：

- 下载最终报告；
- 查看工作流中间结果；
- 审计数据来源和模型行为；
- 定位失败阶段；
- 避免重复执行已经完成的计算；
- 对比不同模型或提示词版本；
- 复现实验与评测结果。

Artifact 在其他场景中的含义：

| 场景 | Artifact 的含义 |
|---|---|
| Agent 工作流 | Pack、报告、Manifest 等运行结果 |
| GitHub Actions | CI 生成并提供下载的 ZIP、测试报告、镜像压缩包 |
| Docker | 构建完成的 Docker Image 也属于构建产物 |
| Python 打包 | wheel、源码压缩包 |
| 测试 | 覆盖率报告、日志、截图、评测结果 |

## 3. 核心业务术语

### 3.1 Request：请求

Request 表示用户希望系统完成什么。例如公司、截止日期、报告语言、fast/deep 档位等输入。Request 只是输入，还不是后台任务。

### 3.2 Job：业务任务

Job 表示一次完整的投研业务实例，由 `job_id` 唯一标识。

```text
pending → running → succeeded
                  ↘ failed
                  ↘ cancelled
```

- `pending`：等待执行；
- `running`：正在执行；
- `succeeded`：成功；
- `failed`：失败；
- `cancelled`：用户取消。

### 3.3 Task：子任务 / 队列任务

Task 的含义取决于上下文：

- CrewAI Task：分配给 Research、Analysis、Writer Agent 的工作；
- Celery Task：消息队列中等待 Worker 执行的异步任务；
- 开发 Task：路线图中的一个开发任务。

Job 通常表示一次完整业务，Task 表示 Job 内部的一项工作或一条队列消息。

### 3.4 Step：步骤

Step 是可以单独记录状态、耗时和错误的工作流阶段：

```text
00_request
01_company_resolve
02_research
03_documents
04_analysis
05_writer
06_quality
07_manifest
```

Step 用于显示进度、记录重试次数、定位失败阶段和计算阶段耗时。

### 3.5 Flow / Workflow：工作流

Workflow 表示完整的业务执行过程；Flow 更常指代码中的流程实现。例如：

```text
公司识别 → 信息搜索 → 文档解析 → 财务分析 → 报告撰写 → 质量门禁 → 发布报告
```

### 3.6 Agent：智能体

Agent 是大模型驱动的执行单元：

```text
Agent = LLM + 角色 + 提示词 + 工具 + 行为规则 + 迭代预算
```

本项目主要包括 Research Agent、Analysis Agent 和 Writer Agent。Agent 不等于模型；模型只是 Agent 的一个组成部分。

### 3.7 Crew：Agent 团队

Crew 是 CrewAI 中多个 Agent 和 Task 的组合。本项目使用顺序模式：

```text
Research Agent → Analysis Agent → Writer Agent
```

### 3.8 Tool：工具

Tool 是 Agent 可以调用的确定性能力，例如 SEC 查询、搜索、财务计算、ArtifactReader 和 CitationVerifier。LLM 决定何时调用，工具代码负责真正执行。

### 3.9 Function Calling / Tool Calling：函数调用 / 工具调用

模型先输出结构化参数，框架再调用真实函数。例如：

```json
{"artifact_key": "research_pack"}
```

框架把它转换为：

```python
ArtifactReader(artifact_key="research_pack")
```

工具调用参数属于执行过程，不是 Agent 的最终答案。

## 4. 上下游数据术语

### 4.1 Pack：结构化数据包

Pack 是本项目对结构化中间数据的命名，不表示 ZIP 压缩包。例如：

- `ResearchPack`：研究信息包；
- `FinancialAnalysisPack`：财务分析包；
- `ReportDraft`：报告草稿结构。

### 4.2 Schema：结构规则

Schema 规定数据有哪些字段、字段类型、必填项和约束。例如 `ReportDraft` 必须包含 `version`、`title` 和 `markdown`。

Schema 在不同场景中的含义：

| 场景 | 含义 |
|---|---|
| Pydantic / JSON | 一个对象允许有哪些字段 |
| PostgreSQL | 数据库有哪些表、列和约束 |
| API | 请求和响应的数据格式 |

### 4.3 Contract：契约

Contract 是上下游共同遵守的完整约定。Schema 主要描述结构，Contract 还包括字段语义、错误类型、状态规则、是否允许为空以及上下游责任。

### 4.4 Boundary：边界

Boundary 是数据从一个模块进入另一个模块时的检查位置。`PackBoundary` 会区分最终答案与工具参数、解析 JSON、校验字段和业务语义，再将结果转换成 Pack。

### 4.5 Validation：校验

Validation 用于验证数据是否符合规则，例如标题不能为空、版本必须存在、状态与内容必须一致。校验失败通常产生 `ValidationError`。

### 4.6 Guardrail：护栏

Guardrail 是对 Agent 行为和输出的保护机制，例如禁止伪造财务数据、禁止无限修复、要求引用存在。Schema 是结构规则，Guardrail 更偏向行为、安全和质量规则。

### 4.7 Structured Output：结构化输出

结构化输出是可以被程序解析和校验的 JSON/Pydantic 对象，而不是自由文本。它便于下游使用、数据库保存、自动校验和 UI 展示。

### 4.8 Serialization / Deserialization：序列化 / 反序列化

- Serialization：Python 对象转换为 JSON 字符串；
- Deserialization：JSON 字符串恢复为 Python 对象。

### 4.9 Manifest：清单

Manifest 是一次运行的总清单，通常记录任务状态、Artifact 列表、模型与提示词版本、校验结果、耗时、Token 和来源摘要。它相当于本次执行的目录与审计说明。

### 4.10 Citation / Locator：引用 / 定位信息

- Citation：某条事实或数字对应的来源；
- Locator：来源中的具体位置，例如页码、表名、章节、HTML 元素或 XBRL concept。

## 5. 稳定性术语

### 5.1 Retry：重试

临时失败后再次尝试。网络超时、429、502/503 适合重试；API Key 错误、参数缺失和永久 Schema 冲突通常不适合重试。

### 5.2 Timeout：超时

一个操作允许等待的最长时间。超过时间仍未完成，就终止并报告超时。

### 5.3 Iteration / max_iter：迭代 / 最大迭代次数

一次迭代通常是一轮“思考 → 调用工具或输出答案”。`max_iter=5` 表示最多允许五轮。它不是请求超时时间，也不一定等于工具调用次数。

### 5.4 Fail-fast：快速失败

发现配置或数据明显错误后立即停止，例如 live 模式缺少 API Key。目的是避免系统带着错误继续运行。

### 5.5 Fallback：降级 / 备用方案

主要方案失败后切换备用方案，例如 HTML 解析失败后尝试 PDF。live 模式不能偷偷降级为 fake，否则会产生虚假成功。

### 5.6 Idempotency：幂等

同一个请求重复提交，不重复创建业务结果。相同 Idempotency-Key 和相同请求内容应复用原 Job；相同 Key 但请求内容不同应返回 409。

### 5.7 Fingerprint：指纹

对规范化请求内容计算的稳定标识，用于判断两个请求是否真正相同。

### 5.8 Lease：租约

Worker 领取任务后获得一段时间的执行所有权。Worker 崩溃或租约过期后，其他 Worker 可以接手，避免永久卡死。

### 5.9 Stale / Stale Recovery：陈旧状态 / 失联恢复

Job 长期为 running，但执行它的 Worker 已不存在，就是 stale running。系统会将其收口为 failed，并清理虚假的 running Step。

### 5.10 Transactional Outbox：事务发件箱

在同一个数据库事务中同时写入业务数据和待投递消息，解决“数据库写成功，但 Redis/Celery 消息发送失败”的不一致窗口。

### 5.11 Relay：中继器

Relay 扫描 Outbox 中的 pending 消息，将其投递到 Redis/Celery，然后标记为 sent。

### 5.12 Worker：工作进程

Worker 是后台真正执行任务的进程：从队列取 Job、执行 Flow、更新 PostgreSQL 并生成 Artifact。Worker 不是 Agent。

### 5.13 Queue / Broker：队列 / 消息代理

- Queue：等待处理的任务列表；
- Broker：传递和保存队列消息的中间系统；
- Redis：本项目的 Celery Broker；
- Celery：异步任务框架；
- Worker：任务消费者。

### 5.14 Dispatcher：调度 / 投递器

Dispatcher 将业务 Job 转换成异步消息并投递到 Celery 队列。

### 5.15 Checkpoint：检查点

Checkpoint 是工作流中可恢复的保存点。失败后可以从最近检查点继续，而不是重新执行整个流程。本项目暂未完整实现阶段级 Checkpoint。

## 6. 软件架构术语

### 6.1 Domain：领域层

包含 ResearchRequest、Job 状态、Pack、状态转换和业务错误等核心规则，尽量不依赖 FastAPI、SQLAlchemy 或 Redis。

### 6.2 Application：应用层

组织创建任务、取消任务、执行任务、查询工件和幂等处理等业务用例，回答“系统要完成什么业务动作”。

### 6.3 Infrastructure：基础设施层

负责 PostgreSQL、Redis、Celery、SEC、Serper、文件系统、Prometheus 和 OpenTelemetry 等具体技术实现。

### 6.4 API：应用程序接口

前端或其他程序访问后端的入口，例如创建 Job、查询状态、列出 Artifact 和下载报告。

### 6.5 Repository

Repository 有两种常见含义：

- Git Repository：保存源代码和 Git 历史的仓库；
- Repository Pattern：封装数据库读写的接口，使业务层不依赖具体数据库。

### 6.6 Store：存储接口 / 存储实现

Store 负责保存和读取某类数据，例如 JobStore、ArtifactStore、OutboxStore 和 IdempotencyStore。

### 6.7 Port / Adapter：端口 / 适配器

Port 是应用层依赖的抽象接口；Adapter 是基础设施层的具体实现。例如 JobStore 是 Port，SQLAlchemyJobStore 是 Adapter。

### 6.8 Wiring：组装 / 接线

Wiring 把抽象接口和真实实现连接起来，例如 JobStore 接到 SQLAlchemy、Dispatcher 接到 Celery、FlowRunner 接到 LiveResearchFlowRunner。

### 6.9 Dependency Injection：依赖注入

对象不在内部写死依赖，而是从外部接收。测试时注入 FakeStore，生产时注入 SQLAlchemyStore。

### 6.10 DTO：数据传输对象

DTO 是模块之间或 API 中传递数据的简单结构，主要负责传输，不承载复杂业务行为。

### 6.11 Model：模型

Model 在项目中有多种含义：

| 场景 | 含义 |
|---|---|
| LLM Model | Qwen、DeepSeek 等大模型 |
| Pydantic Model | ReportDraft、ResearchRequest 等数据模型 |
| ORM Model | Python 类与数据库表的映射 |
| Domain Model | Job、状态和业务规则 |

### 6.12 ORM：对象关系映射

ORM 将 Python 类映射到数据库表。本项目使用 SQLAlchemy，减少手写 SQL，并保持对象与表结构对应。

### 6.13 Migration：数据库迁移

Migration 是数据库结构的版本变更。`upgrade` 表示升级，`downgrade` 表示回滚。本项目使用 Alembic。

### 6.14 Protocol / Interface：协议 / 接口

描述一个对象必须提供哪些方法，而不关心具体类。只要实现了需要的方法，就可以作为该依赖使用，这也接近鸭子类型思想。

### 6.15 Adapter：适配器

把一个外部系统的接口转换成项目内部统一接口。例如把不同供应商的 OpenAI-compatible API 转换成统一 LLMConfig/LLM 接口。

## 7. Docker 术语

### 7.1 Image：镜像

Image 是只读运行模板，包含代码、Python、依赖、系统库和启动配置。它类似已经装好环境的软件包。

### 7.2 Container：容器

Container 是 Image 启动后的运行实例。同一个 Image 可以启动 API、Worker、Streamlit 等多个 Container。

### 7.3 Dockerfile

Dockerfile 是制造镜像的说明书，规定基础镜像、复制文件、安装依赖和启动配置。

### 7.4 Build Context：构建上下文

`docker build .` 中的 `.` 是 Build Context。Docker 构建过程只能读取这个目录范围内的文件。这里的 Context 与 LLM Context 不是同一个概念。

### 7.5 `.dockerignore`

告诉 Docker 构建时不要复制 `.env`、`.git`、`.venv`、artifacts 等内容，避免密钥进入镜像并减小镜像体积。

### 7.6 Docker Compose

通过 YAML 文件统一定义和启动 API、Worker、PostgreSQL、Redis、Streamlit、Prometheus、Grafana、Jaeger 等服务。

### 7.7 Service：Compose 服务

Compose 文件中的一个运行单元，例如 `api`、`worker`、`postgres`。一个 Service 通常会启动一个 Container。

### 7.8 Volume：数据卷

脱离容器生命周期保存数据的空间，例如 `postgres_data`、`redis_data` 和 `artifacts_data`。`docker compose down -v` 会删除数据卷，因此不能随意执行。

### 7.9 Bind Mount：目录挂载

把宿主机文件或目录直接映射到容器。例如 Prometheus 配置文件通过 Bind Mount 提供。源码未挂载时，修改代码后必须重新构建 Image。

### 7.10 Port Mapping：端口映射

`8501:8501` 左边是 Windows 主机端口，右边是容器端口。浏览器访问 `localhost:8501` 会转发到 Streamlit Container。

### 7.11 Layer：镜像层

Dockerfile 中每个构建步骤可能形成一个 Layer。未变化的 Layer 可以复用缓存，从而加快重新构建。

### 7.12 Registry：镜像仓库

用于保存和分发 Docker Image，例如 Docker Hub 和 GHCR。GitHub 代码仓库与 Docker Registry 不是同一个东西。

### 7.13 GHCR

GitHub Container Registry，GitHub 提供的 Docker 镜像仓库。Ubuntu 可以通过 `docker pull ghcr.io/...` 下载已构建镜像。

## 8. 测试与发布术语

### 8.1 Unit Test：单元测试

测试单个函数或类，速度快，通常不需要真实网络和完整系统。

### 8.2 Integration Test：集成测试

测试多个模块的真实连接，例如 SQLAlchemy Repository 与 PostgreSQL。

### 8.3 E2E：端到端测试

从用户入口测试到最终结果，例如创建任务、队列投递、Worker 执行、保存数据库并生成 PDF。

### 8.4 Smoke Test：冒烟测试

快速确认系统能否启动、健康检查是否正常、能否创建任务和下载报告。它不等于完整测试。

### 8.5 Fake / Mock

- Fake：可运行的简化替代实现，例如不联网的 FakeLLM；
- Mock：重点验证某个调用是否发生以及参数是否正确。

Fake Flow 成功只能证明系统链路可运行，不能证明真实模型和外部 API 有效。

### 8.6 Fixture：测试夹具

测试前准备好的固定数据或环境，例如 SEC JSON、测试 PDF、测试数据库和 Fake LLM 响应。

### 8.7 Benchmark：基准测试

在统一条件下重复运行，统计成功率、吞吐量、P50/P95/P99、Token、成本和失败原因。

### 8.8 Golden Test：黄金样例测试

保存一份确认正确的基准输出，以后生成新结果时与它比较，防止格式或内容结构意外变化。

### 8.9 CI / CD

- CI（持续集成）：提交代码后自动运行 Ruff、mypy、pytest、数据库迁移等检查；
- CD（持续交付/部署）：自动构建镜像并部署到目标环境。

### 8.10 Pipeline：流水线

自动按顺序执行检查、测试、构建、发布和部署的一组任务。

### 8.11 Commit / Branch / Push

- Commit：本地保存的一次版本快照；
- Branch：一条独立开发线；
- Push：把本地 Commit 上传到远程 Git 仓库。

## 9. 可观测性术语

### 9.1 Observability：可观测性

通过系统外部输出理解内部状态，主要包括 Logs、Metrics 和 Traces。

### 9.2 Log：日志

按时间记录具体事件，例如 `job_flow_succeeded`、HTTP 429 和 `SCHEMA_INVALID`。适合查看错误细节。

### 9.3 Metric：指标

可统计的数字，例如任务数、成功率、Token、延迟和正在运行的请求数。

### 9.4 Trace：调用链

表示一次完整请求经过的所有模块，例如 API → Worker → Flow → Agent → Tool。

### 9.5 Span：调用链片段

Trace 由多个 Span 组成。每个 Span 记录操作名称、起止时间、耗时、状态、属性和错误。

### 9.6 Trace ID / Span ID

- Trace ID：整条调用链的唯一编号；
- Span ID：调用链中一个操作的唯一编号。

### 9.7 Prometheus

定期抓取 `/metrics` 并保存时间序列数据，用于查询任务数、错误率、P95 延迟和 Token 等指标。

### 9.8 Grafana

从 Prometheus 查询数据并画成图表。Grafana 通常不产生指标，主要负责展示。

### 9.9 Jaeger

用于查看 Trace，回答某个任务卡在哪里、哪个 Agent 最慢、哪个工具报错。

### 9.10 OpenTelemetry / OTel

统一采集和传输日志、指标与调用链的标准。本项目主要用它把 Span 发到 Jaeger。

### 9.11 Collector / Exporter

- Exporter：把可观测性数据发送出去；
- Collector：接收、处理并转发数据。

### 9.12 Scrape / Target

- Scrape：Prometheus 定期访问 `/metrics` 抓取数据；
- Target：被 Prometheus 抓取的目标，例如 `api:8000` 和 `worker:9101`。

### 9.13 Counter：计数器

只能增加，不能减少，例如 `llm_tokens_total`、`research_jobs_total`。

### 9.14 Gauge：仪表值

可以增加和减少，例如当前运行中的任务数和 HTTP 请求数。

### 9.15 Histogram / Bucket：直方图 / 分桶

Histogram 将耗时放入多个 Bucket，例如 ≤0.1 秒、≤0.5 秒、≤1 秒，用于计算延迟分位数。

### 9.16 P50 / P95 / P99

- P50：50% 的请求不超过该耗时；
- P95：95% 的请求不超过该耗时；
- P99：99% 的请求不超过该耗时。

P95/P99 比平均值更容易暴露慢请求。

### 9.17 Label：标签

用于对指标分类，例如 provider、model、role 和 status。不能使用 job_id、公司名、Prompt 等高基数或敏感内容。

### 9.18 Cardinality：基数

标签组合的数量。status、role 属于低基数；job_id、公司名属于高基数。高基数会显著增加 Prometheus 内存和查询压力。

### 9.19 RED / USE

RED 常用于服务：Rate（请求速率）、Errors（错误）、Duration（耗时）。USE 常用于资源：Utilization（使用率）、Saturation（饱和度）、Errors（错误）。

### 9.20 Health / Readiness

- Health/Liveness：进程是否活着；
- Readiness：数据库、Redis 等依赖是否就绪，服务是否能接收流量。

## 10. LLM 术语

### 10.1 Provider / Vendor：供应商

提供模型 API 的厂商或平台，例如 Qwen/阿里云、DeepSeek。OpenAI-compatible 只是接口协议，不等于供应商。

### 10.2 Model：大模型名称

例如 `qwen3.6-flash`、`deepseek-v4-flash`。供应商和模型名称是两个不同概念。

### 10.3 Base URL

API 请求发送到的服务地址，例如某供应商的 OpenAI-compatible `/v1` 地址。

### 10.4 API Key

证明调用者身份的秘密凭证，不能提交进 Git、写入镜像、打印日志、放入指标标签或粘贴到公开聊天。

### 10.5 Token

模型处理文本的计算和计费单位，不完全等于中文字符或英文单词。

- input/prompt token：发送给模型的输入；
- output/completion token：模型生成的输出；
- cached input token：命中供应商缓存的输入；
- total token：总量。

### 10.6 Context Window：上下文窗口

模型单次请求最多处理的 Token 数。上下文包括 System Prompt、Task、历史消息、工具定义、工具结果和上游 Pack。

### 10.7 Temperature：温度

控制生成随机性。低温度更稳定保守，高温度更发散。财务分析和结构化输出通常使用较低温度。

### 10.8 Thinking / Reasoning：思考模式

模型在输出前进行更长推理，适合复杂分析，不一定适合简单搜索、格式转换和明确的结构化输出。

### 10.9 Usage：用量信息

模型响应中返回的真实资源用量，例如 prompt_tokens、completion_tokens 和 total_tokens。Token 指标应从 Usage 读取，不能根据字符串长度伪造。

### 10.10 Prompt：提示词

告诉模型角色、目标、输入、工具、输出格式、限制和质量要求的指令文本。

### 10.11 System Prompt / User Prompt

- System Prompt：最高层角色与行为规则；
- User Prompt：用户或业务任务的具体要求。

### 10.12 Grounding：事实约束 / 落地依据

要求模型只能根据指定来源、上游 Pack 或检索结果回答，减少幻觉和无来源事实。

### 10.13 Hallucination：幻觉

模型生成看似合理但没有来源、并不真实或无法验证的事实和数字。

### 10.14 Cached Input：缓存输入

供应商识别到重复 Prompt 前缀后复用计算结果。命中缓存通常价格更低、响应更快，但具体规则由供应商决定。

## 11. 最容易混淆的词

| 单词 | 在本项目中可能表示什么 |
|---|---|
| Artifact | Agent 运行产物、CI 产物、Docker 构建产物 |
| Model | LLM 模型、Pydantic 模型、ORM 模型、领域模型 |
| Context | LLM 上下文、Crew Task context、Docker build context |
| Task | CrewAI 任务、Celery 队列任务、开发计划任务 |
| Repository | Git 代码仓库、数据库 Repository 模式 |
| Service | 应用服务类、Docker Compose 服务、外部 API 服务 |
| Image | Docker 镜像、普通图片 |
| Schema | JSON/Pydantic 结构、数据库结构 |
| Migration | 数据库迁移、平台或模型迁移 |
| Worker | Celery 后台进程，不是 Agent |
| Tool | Agent 可调用工具，不等于普通 UI 工具 |
| Cache | 工具结果缓存、LLM Prompt Cache、Docker 构建缓存 |
| Profile | fast/deep 研究档位、系统性能配置、用户资料（需结合上下文） |
| Status | Job 状态、Step 状态、HTTP 状态、业务完整性状态 |

## 12. 快速记忆

```text
Request  是用户输入
Job      是一次完整业务
Task     是分配出去的工作
Step     是可追踪的执行阶段
Agent    是智能执行者
Tool     是确定性能力
Pack     是上下游结构化数据
Artifact 是保存下来的执行产物
Manifest 是产物和运行信息的总清单
Worker   是后台执行进程
Metrics  是统计数字
Trace    是单次请求的完整调用链
```

