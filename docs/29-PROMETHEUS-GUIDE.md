# Prometheus 观测体系使用指南

> 面向本项目开发者的 Prometheus 学习笔记：指标是什么、怎么埋点、多进程怎么做、怎么加新指标、怎么排障。
> 所有代码路径相对项目根 `D:\MyProjects\invest_research_agent`。

---

## 1. 概述：Prometheus 在这里管什么

本项目用 Prometheus 收集两类进程的运行时指标：

- **API 进程**（`api:8000`）：HTTP RED（请求速率/错误/耗时）、任务创建计数。
- **Worker 进程**（`worker:9101`）：LLM 调用、工具调用、Agent 运行、质量门禁、任务成本——**大部分业务指标发生在 Worker**。

Prometheus 只负责**采集与存储**；**可视化用 Grafana**、**链路追踪用 Jaeger**（见第 7 节）。

> 指标不含高基数 label（job_id/公司名/URL）——这是有意的设计：避免每个任务一条 series 把存储撑爆。

---

## 2. 三个指标类型（先搞懂概念）

`prometheus_client` 提供三种核心类型，本项目全部用到：

| 类型 | 语义 | 典型用途 | 本项目例子 |
|---|---|---|---|
| **Counter**（计数器） | 只增不减（重启清零） | 次数累计 | `llm_requests_total`、`job_tokens_total` |
| **Histogram**（直方图） | 观测值分布，自带 `_bucket/_sum/_count` | 耗时 P50/P95 | `llm_request_duration_seconds` |
| **Gauge**（仪表） | 可增可减的当前值 | 当前并发 | `research_jobs_in_progress` |

**Counter 的命名约定**：名字以 `_total` 结尾，PromQL 里可以 `rate(x_total[5m])` 算每秒速率，或 `sum(x_total)` 算累计。

**Histogram 为什么能算 P95**：`histogram_quantile(0.95, sum(rate(x_bucket[5m])) by (le))`——它把观测值扔进预定义的桶（bucket），从桶分布近似分位数，不需要存每条原始数据。

---

## 3. 本项目指标体系（`src/invest_research/infrastructure/observability/metrics.py`）

约 30 个指标，按主题分组：

| 主题 | Counter | Histogram | Gauge |
|---|---|---|---|
| **任务** | `research_jobs_total` | `research_job_duration_seconds` | `research_jobs_in_progress` |
| **步骤** | `workflow_steps_total` | `workflow_step_duration_seconds` | — |
| **LLM** | `llm_requests_total`、`llm_tokens_total`、`llm_usage_missing_total` | `llm_request_duration_seconds` | — |
| **工具** | `tool_calls_total`、`tool_retries_total`、`tool_cache_total`、`tool_budget_exhausted_total` | `tool_duration_seconds` | — |
| **Agent** | `agent_runs_total`、`agent_iteration_limit_total` | `agent_duration_seconds` | — |
| **质量** | `quality_gate_failures_total`、`pack_validation_total`、`schema_repair_total`、`analysis_completeness_total`、`revision_total` | — | — |
| **WS3 成本** | `job_tokens_total`、`job_tool_calls_total`、`job_cost_usd_total` | — | — |
| **HTTP** | `http_requests_total` | `http_request_duration_seconds` | `http_requests_in_progress` |
| **恢复/故障** | `stale_recovery_total`、`failure_total`、`writer_recovery_total` | — | `stale_running_steps` |

> 每个指标都有 label 白名单（如 `llm_requests_total` 只有 `provider/model/role/status`），保证低基数。

---

## 4. 进程架构：API 单进程 vs Worker 多进程

这是本项目观测设计的核心难点：**API 和 Worker 是独立进程，而 Worker 是 Celery prefork（一个父进程 fork 出多个子进程）**。

### 4.1 单进程模式（API）

API 进程直接使用 `prometheus_client` 默认 REGISTRY，`/metrics` 端点用 `generate_latest()` 导出：
- `api/app.py` `/metrics` 处理器 → `generate_latest()`（默认 REGISTRY）。
- 所有打点落在同一进程内存，Prometheus 一个 target 就能抓全。

### 4.2 多进程模式（Worker）

`worker_metrics_server.py` 解决了"子进程数据怎么汇总"：

```
Worker 主进程
  ① worker.py:31  os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")
       ↑ 必须在任何 prometheus_client import 之前，否则 Counter 落单进程内存
  ② import metrics.py → Counter 以"多进程模式"创建（写 mmap .db 文件）
  ③ worker_init 信号 → 主进程启动 HTTP server(9101) + MultiProcessCollector
       │  fork
       ▼
Worker 子进程（ForkPoolWorker）
  ④ 执行任务时 .inc() → 写 {dir}/{类型}_{pid}.db（一个进程一个 mmap 文件）
  ⑤ worker_process_shutdown → mark_process_dead 删自己 pid 的 .db（防僵尸残留）
```

**关键点**：
- `MultiProcessCollector` 在**每次被 scrape 时**重新扫描目录下所有 `.db` 文件，按 label 求和聚合。
- 所以 `worker:9101` 聚合了所有子进程的指标，Prometheus 一个 target 抓全。
- `.db` 文件命名 `{metric_type}_{pid}.db`（如 `counter_312.db`），一个进程所有同类型指标共享一个文件。
- 容器重建时 `/tmp` 自动清空，启动时 `cleanup_multiproc_dir` 还会清孤儿 `.db`。

### 4.3 Prometheus 抓取配置

`deploy/prometheus/prometheus.yml` 定义两个 target：

```yaml
scrape_configs:
  - job_name: invest-research-api
    static_configs: [{ targets: ["api:8000"] }]
  - job_name: invest-research-worker
    static_configs: [{ targets: ["worker:9101"] }]
```

两个 target 的同名指标（如 `llm_requests_total`）由 Prometheus 自动合并求和。每个 target 有 `metric_relabel_configs` 白名单（`action: keep`）——**只保留白名单内的指标**，防止把无关/高基数指标存进来。

---

## 5. 怎么打点（埋点）

### 5.1 三层结构

业务代码**不直接**操作 `metrics.py` 的 Counter，而是调 `metrics_events.py` 的打点函数：

```
业务代码（worker.py / flow_wiring.py / observer）
   ↓ 调
metrics_events.py（打点函数：校验 + 脱敏 + 吞异常）
   ↓ 内部
metrics.py（Counter/Histogram/Gauge 定义）
```

这样做的好处：
- **脱敏**：打点函数只传 label 白名单值，业务代码不暴露敏感字段。
- **容错**：`_safe_inc` 把任何异常吞掉（只记 `metrics_inc_failed` 日志），**打点失败绝不中断业务**。

### 5.2 核心打点辅助函数（`metrics_events.py`）

| 函数 | 打哪个指标 | 何时调用 |
|---|---|---|
| `count_research_job(status)` | `research_jobs_total` | 任务 pending/running/succeeded/failed |
| `observe_research_job(profile, status, duration)` | `research_job_duration_seconds` | 任务终态 |
| `count_llm_request(provider, model, role, status)` | `llm_requests_total` | 每次真实 LLM 调用完成 |
| `observe_llm_duration(...)` | `llm_request_duration_seconds` | 同上 |
| `count_llm_tokens(provider, model, role, type, amount)` | `llm_tokens_total` | 同上 |
| `count_llm_usage_missing(...)` | `llm_usage_missing_total` | LLM 响应无 usage |
| `count_tool_call(tool, status)` | `tool_calls_total` | 每次工具执行 |
| `observe_tool_duration(tool, status, s)` | `tool_duration_seconds` | 同上 |
| `count_agent_run(role, profile, provider, model, status)` | `agent_runs_total` | Agent 运行结束 |
| `record_job_performance(...)` | `job_*` 三个 | 任务结束后读 manifest 算 token/成本 |

### 5.3 打点位置对照（去哪埋点）

| 打点位置 | 覆盖什么 |
|---|---|
| `worker.py:519-546` | 任务 running→终态（`count_research_job`/`observe_research_job`） |
| `worker.py:641-681` `_record_execution` → `_record_job_perf_metrics` | WS3 成本（`record_job_performance`） |
| `llm_full_observer.py:750` | LLM 调用（CrewAI 事件总线 / Finalizer 观测器） |
| `llm_call_observer.py:280` | LLM usage missing |
| `real_tools.py` | 工具调用/缓存/预算耗尽 |
| `flow_wiring.py` | Agent 运行/步骤/质量门禁/包校验 |

### 5.4 代码示例：加一个"翻译调用计数"

```python
# 1. metrics.py 定义 Counter（低基数 label）
translation_calls_total = Counter(
    "translation_calls_total",
    "官方声明翻译调用次数（按结果）",
    ["status"],  # success / failure
)

# 2. metrics_events.py 加打点函数（先校验再 inc，吞异常）
def count_translation_call(status: str) -> None:
    if status not in ("success", "failure"):
        return
    _safe_inc(translation_calls_total, label_values=(status,))

# 3. 业务代码调用（翻译失败也不影响主流程）
count_translation_call("success" if ok else "failure")

# 4. 别忘了把 translation_calls_total 加进 prometheus.yml 白名单 regex！
```

---

## 6. 怎么新增一个指标（实操步骤清单）

1. `metrics.py` 定义指标对象（选对类型：次数→Counter，耗时→Histogram，当前值→Gauge）。
2. `metrics_events.py` 加打点函数（先校验 label 合法性，再 `_safe_inc`/`_safe_obs`）。
3. 业务代码调用打点函数。
4. **把指标名加进 `deploy/prometheus/prometheus.yml` 两个 job 的 `metric_relabel_configs` 白名单 regex**（最容易漏的一步，见第 9 节事故）。
5. 如果要做 Grafana 面板：`deploy/grafana/provisioning/dashboards/research.json` 加 panel。
6. 补测试：`tests/test_metrics_events.py`（打点函数）、`tests/test_grafana_dashboard.py`（面板 PromQL）、`tests/test_prometheus_labels.py`（低基数校验）。

---

## 7. 看板与链路：Grafana / Jaeger

### 7.1 Grafana（`localhost:3000`）

数据源是 Prometheus。`research.json` 已有 8 行面板：

```
系统健康 / HTTP RED / 任务与步骤 / Agent / PackBoundary / 工具与缓存 / LLM / 成本与用量(WS3)
```

只要 Prometheus 有数据，面板自动展示。新增面板 = 在 research.json 加一个 panel 对象（填 PromQL 查询）。

### 7.2 Jaeger（`localhost:16686`）

链路追踪用 OpenTelemetry（OTLP）：

```
应用（API/Worker） → otel-collector:4318 → otlp/jaeger exporter → Jaeger:4317
```

配置：`deploy/otel-collector.yaml` + `src/invest_research/infrastructure/observability/tracing.py`（`setup_tracing`）。

**prefork 关键坑**：Celery fork 后子进程不会继承父进程的 OTel 导出线程，所以 `worker_metrics_server.py::_setup_child_tracing` 在每个 `worker_process_init` 里重建 TracerProvider，否则 span 永远留在内存不上传。

**加自定义 span**（如翻译调用）：用 `tracer.start_as_current_span("annual.translate")` 包住要追踪的调用。

---

## 8. 本地访问地址

| 服务 | 地址 | 说明 |
|---|---|---|
| Prometheus | `http://localhost:9090` | 查询/图表（`/api/v1/query?query=...`） |
| Grafana | `http://localhost:3000` | 看板 |
| Jaeger | `http://localhost:16686` | 链路追踪 |
| API metrics | `http://localhost:8001/metrics` | 单进程指标 |
| Worker metrics | 容器内 `worker:9101/metrics` | 多进程聚合指标 |

常用 PromQL：

```promql
# LLM 调用速率（5 分钟）
rate(llm_requests_total[5m])
# LLM P95 耗时
histogram_quantile(0.95, sum(rate(llm_request_duration_seconds_bucket[5m])) by (le))
# 任务累计 token（WS3）
sum(job_tokens_total)
# 当前进行中的任务
research_jobs_in_progress
```

---

## 9. 排障手册（含真实事故）

### 事故：WS3 `job_*` 指标为空（2026-08-29 真实测试发现）

**症状**：`job_tokens_total` / `job_tool_calls_total` / `job_cost_usd_total` 在 Prometheus 查询为空，但 API 的 `performance` 字段有 token 数据。

**排查过程**：
1. 怀疑 worker 没记录 → 直接调 `_record_job_perf_metrics` 验证：`.inc()` 成功无异常。
2. 怀疑多进程没写 `.db` → 直接读 `.db`：`MultiProcessCollector` 能收集到 `job_tokens`。
3. 怀疑 9101 没聚合 → python 请求 `worker:9101/metrics`：**有** `job_tokens_total{...} 150`。
4. 定位：**`prometheus.yml` 的 `metric_relabel_configs` 白名单 regex 漏了 `job_*` 三个指标**，Prometheus 抓取时 `action: keep` 把它们丢弃。
5. 修复：白名单 regex 补上 `job_tokens_total|job_tool_calls_total|job_cost_usd_total`，重启 Prometheus，指标出现。

**教训**：**Prometheus 能查到的指标 = 打点成功 + `.db` 聚合成功 + 白名单放行**，三环缺一不可。加新指标最容易漏的是白名单这一环。

### 常用排障步骤

| 症状 | 检查 |
|---|---|
| 指标在 Prometheus 查不到 | ① 直接请求 `worker:9101/metrics` 或 `api:8001/metrics` 看有没有 → ② 没有则查打点是否触发（`metrics_inc_failed` 日志）→ ③ 有则查 prometheus.yml 白名单 |
| 多进程指标不更新 | `/tmp/prometheus_metrics` 有没有 `.db`；worker 重启后是否清空 |
| 打点异常被吞 | worker 日志搜 `metrics_inc_failed` |
| Grafana 面板空 | 先确认 Prometheus 该指标有数据（第 8 节 PromQL） |

---

## 10. 学习自测

1. Counter 和 Gauge 的区别？为什么耗时用 Histogram？
2. 为什么 Worker 需要 `PROMETHEUS_MULTIPROC_DIR`？不设置会怎样？
3. `.db` 文件是谁写的、谁读的、什么时候清理？
4. 加一个指标需要哪 4 步？最容易漏哪步？
5. `rate(x_total[5m])` 和 `sum(x_total)` 各算什么？
6. `histogram_quantile` 为什么不需要存原始数据？
7. API 和 Worker 的同名指标（如 `llm_requests_total`）怎么合并的？
8. Celery fork 后 OTel span 为什么不自动上传？怎么修？
