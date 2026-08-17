# P06-09C 可观测性增强验收文档

> 时间：2026-08-18（本地验证）
> 分支：`agent/m2-deterministic-tools`
> P06-09C 未 push 任何 commit（与交接约束一致），P06-10 未开始。

## 1. 任务目标

在 P06-06C 已有基础指标（research_jobs_total / workflow_steps_total /
tool_calls_total / tool_retries_total / quality_gate_failures_total /
stale_running_steps）之上，补齐 HTTP RED、Job+profile、stale_recovery、
failure、Agent、PackBoundary、工具缓存、LLM 全量指标接线，重构 Grafana
dashboard 为 7 个 Row 分组，并提供可重复运行的隔离 fake 栈 observability
smoke 脚本验收 Prometheus / Grafana / Jaeger 数据链。

## 2. 修改文件

### 代码
- `src/invest_research/infrastructure/observability/metrics.py`：新增 18 个指标
  （http_requests_total / http_request_duration_seconds / http_requests_in_progress /
  research_job_duration_seconds / research_jobs_in_progress / stale_recovery_total /
  failure_total / agent_runs_total / agent_duration_seconds / pack_validation_total /
  schema_repair_total / analysis_completeness_total / tool_duration_seconds /
  tool_cache_total / llm_requests_total / llm_request_duration_seconds /
  llm_tokens_total / llm_usage_missing_total）。
- `src/invest_research/infrastructure/observability/metrics_events.py`：脱敏写入辅助
  （全 label 白名单过滤、value<0 不写、异常仅记脱敏日志）。
- `src/invest_research/api/app.py`：HTTP RED 中间件（路由模板 label，不暴露 job_id）。
- `src/invest_research/infrastructure/queue/worker.py`：
  - Job 终态计数 / 耗时 / 执行中 Gauge / failure 分类（mark_running/succeeded/failed）；
  - **P06-09C-fix**：`PROMETHEUS_MULTIPROC_DIR` 提前到模块导入最前设置，
    否则 metrics.py 先于环境变量读取 → prometheus_client 走单进程模式，
    子进程不写 .db、父进程 9101 聚合拿不到业务指标（本次 smoke 实测发现并修复）。
- `src/invest_research/infrastructure/flow_wiring.py`：agent span 用
  `get_tracer("agent").start_as_current_span("agent.{role}", ...)` 挂到 flow.run 子 span；
  PackBoundary span（pack.boundary.*）。
- `src/invest_research/application/execution.py`：job_flow_succeeded /
  job_flow_failed 结构化日志事件（extra 带 job_id/stage/error_code/trace_id/span_id）。
- `src/invest_research/infrastructure/observability/logging.py`：合并 P05-05
  脱敏（mask_secrets/sanitize_value/setup_structlog）与 P06-09C
  structured_extra/span_id_from_context（修复阶段 3 误覆盖回归）。

### 配置与工具
- `deploy/prometheus/prometheus.yml`：scrape 白名单加入全部新指标与 Histogram 后缀。
- `deploy/grafana/provisioning/dashboards/research.json`：7 个 Row 分组
  （系统健康 / HTTP RED / 任务与步骤 / Agent / PackBoundary / 工具与缓存 / LLM），
  全部 datasource uid=prometheus、timezone=browser、refresh=30s；
  每个 Histogram 用 `_bucket` + `histogram_quantile`；不含 job_id/company/error_message label。
- `scripts/run_observability_smoke.py`：可重复 smoke（仅本地 Docker API，
  FLOW_MODE=fake、占位 key、不删 volume、不 down -v）。
- `deploy/compose.observability-smoke.yml`：独立 fake 栈 compose（独立端口/卷/网络，
  不触碰 live 栈）。

### 测试
- `tests/test_grafana_dashboard.py`：7 Row 顺序、datasource uid、Histogram _bucket、
  指标名存在、无高基数 label、timezone browser。
- `tests/test_metrics_events.py`：扩展 HTTP RED / Job 转换 / Agent Histogram /
  Pack 指标 / 工具缓存 / LLM usage 可用与缺失 / label 白名单。
- `tests/test_prometheus_labels.py`（新增）：Prometheus 白名单含全部指标与
  Histogram 后缀、指标无高基数 label、dashboard PromQL 无禁用 label。

## 3. 指标清单（P06-09C 新增 18 个）

| 分类 | 指标 | label |
|---|---|---|
| HTTP RED | http_requests_total | method, route, status_class |
| | http_request_duration_seconds | method, route |
| | http_requests_in_progress | method, route |
| Job | research_job_duration_seconds | profile, status |
| | research_jobs_in_progress | profile |
| | stale_recovery_total | result |
| | failure_total | stage, error_code |
| Agent | agent_runs_total | role, profile, provider, model, status |
| | agent_duration_seconds | role, profile, provider, model, status |
| PackBoundary | pack_validation_total | stage, pack_type, result, error_code |
| | schema_repair_total | stage, pack_type, result |
| | analysis_completeness_total | status |
| 工具/缓存 | tool_duration_seconds | tool, status |
| | tool_cache_total | tool, result |
| LLM | llm_requests_total | provider, model, role, status |
| | llm_request_duration_seconds | provider, model, role, status |
| | llm_tokens_total | provider, model, role, type |
| | llm_usage_missing_total | provider, model, role |

所有指标 **不含** job_id / company / error_message / url 高基数 label。

## 4. Grafana 面板清单（7 Row）

1. 系统健康：Prometheus Targets Up（stat）、Stale Running Steps（stat）、
   Stale Recovery（stat）、Quality Gate Failures。
2. HTTP RED：Request Rate by Status Class、Request Duration P95、In-Flight Requests。
3. 任务与步骤：Jobs Rate by Status、Job Duration P95、Jobs In-Flight、
   Failure Rate by Stage/Error、Steps Rate、Step Duration P95。
4. Agent：Agent Runs Rate、Agent Duration P95。
5. PackBoundary：Pack Validation Rate、Schema Repair Rate、Analysis Completeness Rate。
6. 工具与缓存：Tool Calls Rate、Tool Retries、Tool Duration P95、Tool Cache Rate。
7. LLM：LLM Request Rate、LLM Duration P95、Token Rate by Type、Usage Missing Rate。

## 5. 调用链（trace）

- api.request（FastAPI 中间件）→ worker.process（Celery task，trace 经 Redis 队列
  carrier 关联）→ flow.run（LiveResearchFlowRunner `_run_live`）→
  agent.{role}（research/analysis/writer，start_as_current_span 子 span）。
- 工具调用 span（pack.boundary.* 等）。
- 结构化日志：job_flow_succeeded / job_flow_failed 事件 extra 带 trace_id/span_id，
  与 Jaeger 链路同 id 可关联。

## 6. 验收实测（隔离 fake 栈）

启动：`docker build -t invest-research:phase5 .`
`docker compose -p obs-smoke -f deploy/compose.observability-smoke.yml \
  --profile observability up -d`

运行 smoke：
`python scripts/run_observability_smoke.py --api-base http://localhost:18000 \
  --prometheus http://localhost:19090 --jaeger http://localhost:16687 \
  --grafana http://localhost:13000 --report reports/obs-smoke.json`

输出（PASS）：

```
[obs-smoke] 已创建 20 个 job（fast=10，deep=10）
[obs-smoke] 幂等复用=OK | 幂等冲突=OK | 取消=OK
[obs-smoke] 终态分布：{"cancelled": 1, "succeeded": 20}
[obs-smoke] Prometheus 非空=True
   {'research_jobs_total': True, 'http_requests_total': True,
    'workflow_steps_total': True, 'up_api': True, 'up_worker': True}
[obs-smoke] Jaeger services=['invest-research-api', 'invest-research-worker',
   'jaeger-all-in-one'] ok=True
[obs-smoke] Grafana ds=1 uids=['invest-research-red'] ok=True
[obs-smoke] 总体结果：PASS
```

### 6.1 Prometheus 关键指标非空（sum by (status)）

```
research_jobs_total {status=pending}    44   （含幂等/取消场景创建总数）
research_jobs_total {status=running}    21
research_jobs_total {status=succeeded}  20
research_jobs_total {status=cancelled}  4
```

### 6.2 Worker 多进程指标修复证据

- 修复前 worker:9101 端点查不到 research_jobs_total，multiproc 目录空；
- 修复（worker.py 模块导入最前设置 PROMETHEUS_MULTIPROC_DIR）后：
  `/tmp/prometheus_metrics/` 出现 48 个 pid_*.db（子进程真实写入），
  Prometheus 能聚合出 worker 侧 succeeded/running/failed 计数。

### 6.3 Worker 日志无真实外部调用

```
external_call_hits: NONE   （无 sec.gov / serper.dev / chat/completions）
```

### 6.4 测试与静态检查

- `test_grafana_dashboard.py` + `test_metrics_events.py` + `test_prometheus_labels.py`
  + `test_execution_service.py`：41 passed；
- `ruff check`（受影响文件）：通过；
- `mypy src/invest_research`：Success（115 files）。

> 说明：全量 pytest 在本地与正在运行的 live Docker 栈（testcontainers 拉
> PostgreSQL service 容器）冲突导致超时，未跑完；CI 已配置全量离线测试
> （SKIP_DB_TESTS=1 893 passed），本次改动不新增 DB 集成依赖。

## 7. 已知限制

- smoke 的"故障注入（schema 错误 / Pack 修复成功 / 修复失败）"在 fake flow
  无注入路径，经 `--include-component-scenarios` 开关默认关闭；组件级场景由
  tests/test_pack_contracts.py 单独覆盖（不联网、确定性）。
- 取消场景存在竞态：极少数情况下任务在取消前已被 worker 消费完成，终态为
  succeeded 而非 cancelled；smoke 对取消只断言 DELETE 返回 200，不要求终态为
  cancelled（终态分布仅统计，不影响 PASS）。
- Jaeger 为 all-in-one 内存存储，重启丢失链路（仅本地查看）。
- 本机未跑全量 pytest（与 live Docker testcontainers 端口冲突超时）；CI 覆盖。

## 8. 是否产生真实外部调用

- 隔离 fake 栈：FLOW_MODE=fake、LLM_API_KEY/SERPER_API_KEY 均为占位符；
- worker 日志无 sec.gov/serper.dev/chat/completions；
- smoke 只创建 fake 任务（OBS-SMOKE-* 假公司名），不触发真实 SEC/Serper/LLM。

## 9. 相关 git commit（均未 push）

| commit | 说明 |
|---|---|
| 76d0ab6 | feat(p06-09c): Jaeger trace and structured log correlation |
| 4a20e06 | feat(p06-09c): restructure grafana dashboard into 7 rows |
| 70a65b4 | test(p06-09c): add observability smoke script |
| 7874f7b | fix(p06-09c): set PROMETHEUS_MULTIPROC_DIR before metrics import |
| 7785663 | test(p06-09c): extend metrics_events, add prometheus_labels + restore logging |

P06-10（100 次基准）未开始。