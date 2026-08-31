# 自动化投研系统：一次请求到最终报告的完整流程

> 本文根据当前项目真实代码整理，用于理解一次前端任务从创建、排队、执行，到最终生成 Markdown/PDF 报告的全过程。
>
> 文中“Redis”指项目中的消息队列服务；Prometheus、Grafana、OpenTelemetry、Jaeger 属于可选的可观测性组件。

## 1. 一句话理解整个系统

前端只负责提交请求和展示结果；FastAPI 负责接收、验证、落库和投递；Redis 只负责传递任务消息；Celery Worker 真正执行投研流程；PostgreSQL 保存任务状态；工件目录保存 JSON、Markdown 和 PDF 文件；最后前端再通过 FastAPI 读取和下载报告。

## 2. 系统总体结构

```text
用户浏览器
   │
   ▼
Streamlit 前端 :8501
   │  HTTP POST / GET
   ▼
FastAPI 后端 :8000
   │
   ├── PostgreSQL
   │     ├── Job 状态
   │     ├──步骤状态
   │     ├──幂等键
   │     ├──Outbox 事件
   │     └──工件目录登记
   │
   └── Redis / Celery 队列
           │
           ▼
      Celery Worker
           │
           ▼
   Research Flow / CrewAI
      ├──公司解析
      ├──SEC、Serper 数据获取
      ├──Research Agent
      ├──Analysis Agent
      ├──Writer Agent
      ├──质量门禁
      └──Markdown/PDF 生成
           │
           ├──文件写入 artifacts volume
           └──状态与工件信息写回 PostgreSQL

前端继续轮询 FastAPI
   │
   ▼
查看 Markdown / 下载 PDF
```

四个最重要的数据职责：

- **PostgreSQL**：系统事实来源，保存任务、步骤和工件的状态与元数据。
- **Redis**：消息邮箱，告诉 Worker“有一个 job_id 需要执行”。
- **artifacts 工件目录**：保存真正的 JSON、Markdown 和 PDF 文件。
- **FastAPI**：前端访问系统能力的唯一入口。

## 3. 各个组件分别负责什么

### 3.1 Streamlit 前端

访问地址通常是 `http://localhost:8501`。

前端负责：

- 输入公司名称或 ticker；
- 选择截止日期、语言、SEC 表单类型；
- 选择 `fast` 或 `deep` 档位；
- 创建任务；
- 保存当前 `job_id`；
- 定时查询任务状态；
- 展示步骤、错误、耗时；
- 预览 Markdown 报告、下载 PDF；
- 展示系统健康状态。

前端不负责：

- 不直接访问 PostgreSQL；
- 不直接访问 Redis；
- 不直接启动 Worker；
- 不直接调用 CrewAI；
- 不直接访问 SEC 或模型；
- 不自己判断报告是否合格。

### 3.2 FastAPI 后端

访问地址通常是 `http://localhost:8000`。

后端负责：

- 验证 HTTP 请求；
- 处理 `Idempotency-Key`；
- 创建 Job；
- 将 Job 和 Outbox 事件写入 PostgreSQL；
- 把 `job_id` 投递给 Celery；
- 查询任务和步骤状态；
- 取消任务；
- 查询和下载已登记工件；
- 暴露 `/health`、`/readiness` 和 `/metrics`。

FastAPI 不直接执行完整投研任务。它接受任务后立即返回 `job_id`，实际研究由 Worker 异步执行。

### 3.3 PostgreSQL

PostgreSQL 是任务状态的权威来源，主要保存：

- `research_jobs`：任务请求和整体状态；
- `workflow_steps`：00～07 步骤状态；
- `idempotency_keys`：防止重复建任务；
- `outbox_events`：待投递或已投递事件；
- `artifacts`：工件登记信息；
- 其他公司、来源、财务事实、报告等结构化数据表。

PostgreSQL 保存的是报告的元数据和状态，不是 PDF 文件本身。

### 3.4 Redis

Redis 在这里主要充当 Celery Broker。

API 会把类似下面的消息放进 Redis：

```text
任务名：invest_research.process_research_job
参数：job_id
队列：research-jobs
```

Redis 不负责生成报告，也不是最终任务状态的事实来源。任务状态仍以 PostgreSQL 为准。

### 3.5 Celery Worker

Worker 是真正执行任务的后台进程，负责：

- 监听 Redis 的 `research-jobs` 队列；
- 根据 `job_id` 从 PostgreSQL 加载完整请求；
- 把 Job 从 `pending` 改为 `running`；
- 初始化并更新 00～07 步骤；
- 选择 fake 或 live Flow；
- 调用外部数据工具和模型；
- 运行 Research、Analysis、Writer；
- 执行质量门禁；
- 生成 Markdown 和 PDF；
- 把最终状态写回 PostgreSQL；
- 登记工件。

### 3.6 artifacts 工件目录

每个 Job 使用独立目录：

```text
artifacts/<job_id>/
```

典型文件包括：

```text
00_request.json
02_research_pack.json
04_financial_analysis_pack.json
05_report_draft.json
06_quality_report.json
07_manifest.json
08_report.md
09_report.pdf
```

PostgreSQL 的 `artifacts` 表负责登记这些文件；真正的文件内容保存在共享工件卷中。

## 4. Docker Compose 启动过程

执行：

```bash
docker compose up -d
```

系统大致按照下面的依赖顺序启动。

### 4.1 PostgreSQL 和 Redis 启动

Compose 等待它们通过 healthcheck。

### 4.2 migrate 一次性容器运行

执行：

```bash
alembic upgrade head
```

它负责确保 PostgreSQL 表结构与当前代码一致。执行成功后 migrate 容器退出。

### 4.3 FastAPI 和 Worker 启动

二者使用同一个项目镜像，但启动命令不同：

- API 使用 Uvicorn 启动 FastAPI；
- Worker 使用 Celery 命令监听 `research-jobs`。

### 4.4 Streamlit 启动

Streamlit 通过容器内地址 `http://api:8000` 调用后端。

### 4.5 可选观测栈启动

执行：

```bash
docker compose --profile observability up -d
```

才会额外启动 Prometheus、Grafana、OpenTelemetry Collector 和 Jaeger。

## 5. 一次点击“创建任务”的完整时序

下面按真实发生顺序展开。

### 第 1 步：用户填写表单

创建页面收集：

- 公司名称或 ticker；
- 数据截止日；
- 报告语言；
- SEC 表单类型；
- `fast` 或 `deep` 研究档位。

前端先构造 `ResearchRequest`。如果公司为空、日期在未来、语言不合法等，Pydantic 会直接给出输入错误，请求不会发给后端。

### 第 2 步：前端生成 Idempotency-Key

前端为创建请求生成幂等键，用来防止：

- 用户连续点击；
- Streamlit rerun；
- 网络超时后的重复提交；
- 浏览器或 HTTP 客户端重试；
- 同一个逻辑请求被创建成多个 Job。

### 第 3 步：前端发送 HTTP 请求

```http
POST /v1/research-jobs
Idempotency-Key: ...
Content-Type: application/json

{
  "input_company": "Microsoft",
  "as_of_date": "2026-08-19",
  "language": "zh-CN",
  "requested_forms": ["10-K", "10-Q"],
  "research_profile": "fast"
}
```

此时前端只等待任务创建结果，不会同步等待整份报告生成。

### 第 4 步：API 中间件记录请求

请求经过 FastAPI 中间件，记录：

- HTTP 方法和路由；
- 状态码；
- 请求耗时；
- Prometheus 指标；
- OpenTelemetry Trace。

### 第 5 步：后端再次校验请求

FastAPI/Pydantic 再次验证请求体。前端校验用于改善用户体验，后端校验才是系统安全边界。

### 第 6 步：后端检查幂等键

后端查询 `idempotency_keys`：

- key 不存在：创建新任务；
- key 已存在且请求内容一致：返回原来的 `job_id`；
- key 已存在但请求内容不同：返回 HTTP 409。

### 第 7 步：创建 Job 和 Outbox 事件

新任务生成 UUID，并在同一个 PostgreSQL 事务中插入：

```text
research_jobs
  id = job_id
  status = pending
  input_company = 用户输入
  as_of_date = 截止日期
  language = 报告语言
  requested_forms = SEC 表单
  research_profile = fast/deep

outbox_events
  job_id = job_id
  event_type = job_created
  status = pending
```

Job 与 Outbox 事件同事务写入，可以避免“Job 已经创建，但根本没有任何可投递事件”的不一致。

### 第 8 步：API 将任务投递到 Redis

Outbox Relay 找到事件后：

1. 把事件从 `pending` 改成 `claimed`；
2. 调用 Celery `send_task`；
3. 将 `job_id` 发送到 `research-jobs` 队列；
4. 成功后把 Outbox 事件改为 `sent`。

### 第 9 步：API 立即返回

新任务通常返回：

```http
HTTP 202 Accepted

{
  "job_id": "...",
  "status": "pending"
}
```

`202 Accepted` 表示任务已经被接受，不代表报告已经完成。

幂等复用旧任务时通常返回 HTTP 200 和原来的 `job_id`。

### 第 10 步：前端保存 job_id

前端将 `job_id` 保存到：

- Streamlit `session_state`；
- 当前页面 URL 查询参数。

因此刷新或复制 URL 后，前端仍能恢复当前任务上下文。

## 6. Worker 接手后的过程

### 第 11 步：Worker 从 Redis 取消息

Worker 一直监听 `research-jobs`，收到消息后调用：

```text
invest_research.process_research_job(job_id)
```

消息中还可以携带 API 请求的 Trace Context，使 Jaeger 能把 API、队列、Worker 和 Flow 串成同一条链路。

### 第 12 步：Job 从 pending 变为 running

Worker 使用条件更新：

```text
pending → running
```

只有任务当前仍是 `pending` 才能成功。若 Redis 重复投递了相同 `job_id`，第二次执行会因为任务已不是 `pending` 而直接退出，避免重复调用模型和重复生成报告。

### 第 13 步：初始化 00～07 工作流步骤

Worker 在 `workflow_steps` 表中幂等创建：

```text
00_request
01_company_resolve
02_research
03_documents
04_analysis
05_writer
06_quality_gate
07_manifest
```

开始一个步骤时：

```text
step.status = running
research_jobs.current_step = 当前步骤
attempt_count += 1
```

完成后：

```text
step.status = succeeded
step.completed_at = 当前时间
research_jobs.current_step = null
```

### 第 14 步：Worker 从 PostgreSQL 重新加载请求

Redis 消息只携带 `job_id`。Worker 根据 `job_id` 从数据库读取：

- 公司；
- 截止日期；
- 语言；
- SEC 表单；
- `fast/deep` 档位。

然后重新构造 `ResearchRequest`，并把进度写入器和 `job_id` 注入 Flow Runner。

## 7. fake 与 live 两种运行模式

### 7.1 fake 模式

```text
FLOW_MODE=fake
```

特点：

- 不调用真实 SEC；
- 不调用 Serper；
- 不调用真实 LLM；
- 使用确定性占位数据执行 00～07 全流程；
- 适合普通测试、CI 和演示系统控制流。

### 7.2 live 模式

```text
FLOW_MODE=live
```

特点：

- 解析真实公司身份；
- 调用 SEC Submissions；
- 调用 SEC Company Facts；
- 调用 Serper 搜索；
- 调用真实 LLM；
- 执行真实 Research、Analysis、Writer；
- 产生实际网络请求、耗时和模型费用。

当前项目根目录 `.env` 配置为：

```text
FLOW_MODE=live
LLM_VENDOR=deepseek
LLM_ENABLE_THINKING=false
```

因此按当前配置启动的 Worker 会走真实 DeepSeek Live Flow。

## 8. Live Flow 的真实研究步骤

### 第 15 步：01_company_resolve 公司解析与预取

系统先把输入公司解析为：

- ticker；
- CIK；
- 法定公司名称；
- 交易所。

解析使用随应用打包的版本化 SEC 官方 company-ticker 快照
`src/invest_research/resources/sec_company_tickers_snapshot.json`。进程首次使用时加载一次，
随后只做本地精确 ticker、CIK、标准化法定名称和受控别名匹配，不在请求中联网刷新，
也不做模糊猜测。索引未命中或命中不唯一时，Job 在 `01_company_resolve` 失败，且在
Research/Crew/LLM 启动前终止，因此不会为无效公司输入消耗模型 Token。

快照是维护产物，不应在 live 请求中自动更新。需要更新时显式执行：

```powershell
$env:SEC_USER_AGENT = "your-app-name your-email@example.com"
.venv\Scripts\python.exe scripts\update_sec_company_index.py
```

更新后应先运行 `tests/test_company_resolver.py`；快照记录了来源 URL、抓取时间、原始
响应 SHA-256 和条目数，以便复核与复现。

解析成功后并行预取：

- SEC Submissions：10-K、10-Q 等申报记录；
- SEC Company Facts：XBRL 财务事实；
- Serper：公开网络搜索结果。

SEC Submissions 首先读取主响应的 `filings.recent`。对于 JPM 等高频申报主体，SEC
会把较早记录拆到 `filings.files` 指向的分页 JSON；系统会依据 `as_of_date` 从近到远
有界读取（最多 12 页），找到请求的 10-K/10-Q 后立即停止，并按 accession 去重。
若确认截止日前仍为零条目标申报，系统在 Research/Crew/LLM 启动前终止，避免消耗
Token 后才发现没有可信 SEC 来源。

结果写入本次 Job 的工具缓存，并显式传给 Research Agent，减少重复外部调用。

### 第 16 步：02_research Research Agent

Research Agent 可使用的真实工具白名单包括：

- `CompanyResolver`；
- `SECSubmissions`；
- `SECCompanyFacts`；
- `FilingDownloader`；
- `DocumentParser`；
- `WebSearch`。

Research Agent 收集：

- 公司身份；
- SEC 申报来源；
- 公开网络来源；
- 来源 URL；
- 数据截止日期；
- 后续分析所需的上下文。

最终生成结构化 `ResearchPack`。

在 DeepSeek 路径中，大致是：

```text
Research Agent 原始输出
       ↓
DeepSeek JSON Finalizer
       ↓
ResearchSelectionDraft
       ↓
ResearchPackAssembler
       ↓
ResearchPack
```

模型负责研究和选择，本地代码负责结构化校验与最终组装。

### 第 17 步：03_documents 文档处理

需要时会：

- 下载 SEC 申报正文；
- 校验大小、媒体类型和 checksum；
- 解析 HTML 或 PDF；
- 提取有限文本块摘要。

文档处理的数据最终服务于 ResearchPack 和后续分析。

### 第 18 步：04_analysis Analysis Agent

Analysis Agent 不负责自由搜索，它主要处理已经取得的 SEC 财务事实。

只允许使用：

- `FinancialFactQuery`；
- `FinancialCalculator`。

模型选择事实和指标，本地代码负责：

- 从预取事实中取回真实金额；
- 校验期间、单位和来源；
- 执行确定性计算；
- 生成 `FinancialAnalysisPack`。

DeepSeek 路径大致是：

```text
Analysis Agent
       ↓
AnalysisSelectionDraft
       ↓
AnalysisPackAssembler
       ↓
FinancialAnalysisPack
```

这样可以减少模型抄错金额、单位和期间的风险。

### 第 19 步：05_writer Writer Agent

Writer Agent 只能读取：

- `ResearchPack`；
- `FinancialAnalysisPack`；
- 固定报告章节要求。

它不应该重新搜索或引入新的外部事实。

当前 DeepSeek 路径中，Writer 直接输出 Markdown 正文，再由本地 `ReportDraftAssembler`：

- 校验报告章节；
- 生成标题；
- 提取 citation key；
- 校验内容不是工具参数或拒绝说明；
- 组装 `ReportDraft`。

## 9. 质量门禁和 Manifest

### 第 20 步：06_quality_gate 确定性质量门禁

该步骤由 Python 代码执行，不是让模型自己评价自己。

主要检查：

- ResearchPack、AnalysisPack、ReportDraft 是否存在；
- 必需章节是否齐全；
- 是否存在引用键；
- 是否出现目标价或确定性收益承诺；
- 公司和截止日期是否一致；
- 是否包含非投资建议声明。

输出：

```text
06_quality_report.json
```

主要字段：

- `all_passed`；
- `issues`；
- `warnings`；
- `recommendation`。

### 第 21 步：07_manifest 生成运行清单

Manifest 记录：

- `published` 或 `rejected`；
- 使用的模型；
- Prompt 版本和 hash；
- Pack checksum；
- Agent 和工具耗时；
- token 使用量；
- 工具调用次数；
- 外部调用证据摘要；
- 质量结果；
- 受控反思决策。

输出：

```text
07_manifest.json
```

## 10. 最终报告生成与发布

### 第 22 步：写入中间工件

Live Flow 将以下内容写入 `artifacts/<job_id>/`：

```text
00_request.json
02_research_pack.json
04_financial_analysis_pack.json
05_report_draft.json
06_quality_report.json
07_manifest.json
```

### 第 23 步：生成 Markdown 报告

系统把：

- ReportDraft 正文；
- 公司身份；
- 来源清单；
- 数据限制；
- 固定免责声明；
- 数据截止日期；

输入 Jinja2 固定模板，生成：

```text
08_report.md
```

固定模板负责报告外壳和免责声明，Writer Agent 负责报告正文。

### 第 24 步：生成 PDF 报告

系统将最终 Markdown 转为 PDF：

```text
08_report.md
      ↓
MarkdownPdfRenderer
      ↓
09_report.pdf
```

如果 Markdown 或 PDF 渲染失败，任务会进入 `failed`，不会被标记为完整成功。

### 第 25 步：Job 进入终态

正常情况：

```text
running → succeeded
```

同时：

- 写入 `completed_at`；
- 清空 `current_step`；
- 记录任务耗时；
- 更新 Prometheus 指标。

异常情况：

```text
running → failed
```

保存：

- 稳定 `error_code`；
- 脱敏后的 `error_message`；
- `failure_stage`；
- 完成时间。

仍然处于 `running` 的步骤会被收口成 `failed_terminal`。

### 第 26 步：登记工件

`ExecutionRecorder` 扫描 Job 工件目录，计算：

- 文件大小；
- SHA-256；
- artifact type；
- storage URI。

然后写入 PostgreSQL 的 `artifacts` 表。

数据库负责登记文件，工件卷保存真正的文件内容。

## 11. 前端如何获取任务进度

状态页面大约每 2 秒调用：

```http
GET /v1/research-jobs/{job_id}
```

FastAPI 从 PostgreSQL 返回：

- Job 状态；
- 当前步骤；
- 研究档位；
- 步骤状态；
- 尝试次数；
- 步骤耗时；
- 错误码；
- 错误消息；
- 失败阶段；
- 任务总耗时。

只要状态仍是 `pending` 或 `running`，前端继续轮询。

到达以下终态后停止：

```text
succeeded
partial
failed
cancelled
```

因此进度不是 Redis 推给前端的，也不是 Worker 直接推送页面的。实际方式是：

```text
Worker 更新 PostgreSQL
        ↓
前端定时询问 FastAPI
        ↓
FastAPI 查询 PostgreSQL
        ↓
前端刷新状态区域
```

## 12. 前端如何显示和下载报告

报告页面首先调用：

```http
GET /v1/research-jobs/{job_id}/artifacts
```

FastAPI 查询 PostgreSQL 中已经登记的工件。

前端找到：

```text
08_report.md
09_report.pdf
```

然后通过 FastAPI 下载：

```http
GET /v1/research-jobs/{job_id}/artifacts/08_report.md
GET /v1/research-jobs/{job_id}/artifacts/09_report.pdf
```

- Markdown 在 Streamlit 页面直接渲染；
- PDF 以下载按钮形式提供。

FastAPI 会先确认：

- 工件已经登记；
- 工件属于当前 Job；
- `artifact_key` 合法；
- 解析后的路径仍在允许的工件根目录中。

前端不会看到服务器内部真实文件路径。

## 13. 失败流程

Flow 或报告发布抛出异常时：

```text
异常
  ↓
Failure Classifier
  ↓
稳定 error_code + 脱敏消息 + failure_stage
  ↓
Job → failed
  ↓
所有 running 步骤 → failed_terminal
  ↓
前端轮询读取失败信息
```

常见稳定错误分类包括：

- `SCHEMA_INVALID`；
- `TIMEOUT`；
- `NETWORK_TRANSIENT`；
- `RATE_LIMITED`；
- `UPSTREAM_5XX`；
- `AUTH_ERROR`；
- `ITERATION_LIMIT`；
- `STRUCTURED_OUTPUT_UNSUPPORTED`；
- `INTERNAL_BUG`。

Celery Task 自身可能在 Worker 日志中显示异常，但用户看到的任务状态以 PostgreSQL 为准。

## 14. 取消流程

前端调用：

```http
DELETE /v1/research-jobs/{job_id}
```

API 尝试条件更新：

```text
pending → cancelled
running → cancelled
```

取消成功后：

- `running` 步骤改为 `skipped`；
- 后续 `pending` 步骤改为 `skipped`；
- 已经成功的步骤保留；
- 清空 `current_step`。

当前实现属于协作式取消。Live Flow 内部没有明显的每阶段取消检查，因此运行中取消的边界需要后续单独完善和验证。

## 15. 可观测性组件如何参与

可观测性组件不参与报告内容生成，只记录系统运行情况。

### 15.1 Prometheus

Prometheus 定时抓取：

- API 的 `/metrics`；
- Worker 的 `:9101/metrics`。

采集内容包括：

- HTTP 请求次数和耗时；
- Job 成功、失败、取消数量；
- 步骤耗时；
- 工具调用和重试；
- Agent 耗时；
- LLM 请求和 token；
- 质量门禁失败类别；
- 缓存命中；
- 失败类型。

### 15.2 Grafana

Grafana 从 Prometheus 查询指标并展示 Dashboard。

Grafana 不直接读取 PostgreSQL，也不控制 Worker。

### 15.3 OpenTelemetry Collector

API、Worker、Flow 和工具调用把 Trace 发送到 Collector：

```text
API / Worker
     ↓ OTLP HTTP
OpenTelemetry Collector
     ↓ OTLP gRPC
Jaeger
```

### 15.4 Jaeger

Jaeger 用来查看一次请求的完整调用链，例如：

```text
api.request
  └── worker.process
       └── flow.run
            ├── Research Agent
            ├── Analysis Agent
            ├── Writer Agent
            └── Tool / LLM calls
```

即使这些观测组件没有启动，核心投研流程理论上仍能执行。

## 16. 测试工具不会在每次点击任务时运行

下面这些属于开发质量检查，不会因为用户点击“创建任务”而自动执行：

- Ruff：格式与代码规范检查；
- mypy：静态类型检查；
- pytest：单元测试和集成测试；
- Testcontainers：测试时启动临时 PostgreSQL；
- GitHub Actions CI：push 或 pull request 时运行。

本地统一质量命令：

```bash
uv run invest-research-check
```

执行顺序：

```text
ruff format --check
        ↓
ruff check
        ↓
mypy
        ↓
pytest
```

必须区分两个“质量检查”：

- **pytest/Ruff/mypy**：检查项目代码本身；
- **06_quality_gate**：检查某一次生成的投研报告。

## 17. 当前实现的重要边界

### 17.1 当前配置会产生真实外部调用

当前 `.env` 是 `FLOW_MODE=live` 和 `LLM_VENDOR=deepseek`。因此按当前配置运行任务会调用真实 SEC、Serper 和 LLM，可能产生网络耗时与模型费用。

### 17.2 Redis 不是状态数据库

Redis 中的消息是否被消费不代表任务成功。任务整体状态必须以 PostgreSQL 的 `research_jobs.status` 为准。

### 17.3 partial 状态目前没有完整主路径

领域模型定义了 `partial`，但当前 Worker 主执行路径主要写入：

- `succeeded`；
- `failed`；
- `cancelled`。

当前没有明显的 `mark_partial` 主路径。

### 17.4 succeeded 不完全等于质量通过

当前质量门禁会让 Manifest 记录 `published` 或 `rejected`，但最终报告发布服务没有再次阻止 rejected 报告文件写出。

所以当前应这样理解：

- `Job succeeded`：技术执行和报告渲染完成；
- 是否达到内容质量要求：还要查看 `06_quality_report.json` 和 `07_manifest.json`。

### 17.5 Outbox 自动恢复链路未完全接入当前启动入口

代码中存在 `run_outbox_relay()` 恢复辅助组件，但当前 `worker.py` 和 Compose 启动路径没有明显调用它。

因此：

- 正常创建时的即时 Outbox 投递已经接通；
- API 投递 Redis 失败后的 Worker 启动自动补投，不应当视为已经完整接通。

### 17.6 工件登记存在很短的时间窗口

当前顺序大致是：

```text
报告文件生成
    ↓
Job 标记 succeeded
    ↓
ExecutionRecorder 登记 artifacts
```

所以极短时间内可能出现 Job 已经 `succeeded`，但工件列表还没有登记完成。之后刷新即可看到。

## 18. 最终完整时序总结

```text
1. 用户填写表单
2. Streamlit 构造 ResearchRequest
3. Streamlit 生成 Idempotency-Key
4. POST /v1/research-jobs
5. FastAPI 验证请求
6. PostgreSQL 检查幂等键
7. PostgreSQL 写 Job(pending) + Outbox(pending)
8. API 通过 Celery 把 job_id 发到 Redis
9. API 返回 202 + job_id
10. 前端保存 job_id
11. Worker 从 Redis 收到 job_id
12. Job pending → running
13. 初始化 00～07 步骤
14. 从 PostgreSQL 加载完整请求
15. 解析公司并并行预取 SEC/Serper
16. Research Agent → ResearchPack
17. 文档下载与解析
18. Analysis Agent → FinancialAnalysisPack
19. Writer Agent → ReportDraft
20. 确定性质量门禁 → QualityReport
21. 生成 RunManifest
22. 写入 00～07 JSON 工件
23. Jinja2 生成 08_report.md
24. PDF Renderer 生成 09_report.pdf
25. Job running → succeeded/failed
26. ExecutionRecorder 登记工件
27. 前端每约 2 秒查询 Job
28. 前端发现终态后停止轮询
29. 前端查询工件目录
30. 前端预览 Markdown、下载 PDF
```

## 19. 关键代码入口索引

- Docker 服务编排：`compose.yml`
- Streamlit 首页：`frontend/Home.py`
- 创建任务页面：`frontend/pages/1_创建投研任务.py`
- 状态轮询页面：`frontend/pages/2_任务状态.py`
- 报告页面：`frontend/pages/3_报告与工件.py`
- 前端 HTTP 客户端：`src/invest_research/frontend/client.py`
- FastAPI 路由：`src/invest_research/api/app.py`
- 生产依赖组装：`src/invest_research/infrastructure/wiring.py`
- SQL Store：`src/invest_research/infrastructure/db/application_stores.py`
- Celery Dispatcher：`src/invest_research/infrastructure/queue/job_dispatcher.py`
- Celery Task：`src/invest_research/infrastructure/queue/tasks.py`
- Worker 入口：`src/invest_research/infrastructure/queue/worker.py`
- Worker 执行服务：`src/invest_research/application/execution.py`
- fake Flow adapter：`src/invest_research/infrastructure/queue/flow_adapter.py`
- live Flow：`src/invest_research/infrastructure/flow_wiring.py`
- 真实研究工具：`src/invest_research/infrastructure/real_tools.py`
- 进度写入：`src/invest_research/infrastructure/db/progress.py`
- 最终报告发布：`src/invest_research/reporting/artifact_publisher.py`
- 工件登记：`src/invest_research/infrastructure/queue/execution_recorder.py`
- 统一代码质量检查：`src/invest_research/quality.py`
