# P06-13 事故复盘：Prometheus 业务指标"三环"排查

> 2026-08-29 真实 Docker + AMZN annual_deep 测试期间发现：WS3 聚合指标（`job_*`）与
> LLM 指标在 Prometheus 查不到，Grafana 成本面板为空。本文复盘完整排查过程与 3 个叠加根因。
> 学习配套：[docs/29-PROMETHEUS-GUIDE.md](29-PROMETHEUS-GUIDE.md)（Prometheus 使用指南）。

## 1. 症状与影响

- `job_tokens_total` / `job_tool_calls_total` / `job_cost_usd_total` 在 Prometheus 查询**空 result**；
- `llm_requests_total` 也查不到（尽管 worker 日志显示真实 DeepSeek 调用成功）；
- Grafana "成本与用量（per-job，WS3）"行 3 个面板全部无数据。

## 2. 排查过程（三环，每环一次决定性验证）

Prometheus 能查到指标 = **① 打点触发 + ② .db 聚合 + ③ 白名单放行**，三环缺一不可。

### 环 ①：打点是否触发？

直接调用 `_record_job_perf_metrics`（worker.py）喂一个带 run_manifest 的 state：

```python
_record_job_perf_metrics(SimpleNamespace(run_manifest={
    "research_mode": "annual_deep",
    "performance": {"token_usage": {"total_tokens": 150, ...}},
}, request=SimpleNamespace(research_profile="deep")))
```

结果：`.inc()` 无异常、无 `metrics_inc_failed` 日志 → **打点代码执行成功**。❌ 排除。

### 环 ②：.db 是否聚合？

直接读 worker 多进程 `.db` + 请求 `worker:9101/metrics`：

```python
from prometheus_client import CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector
reg = CollectorRegistry(); MultiProcessCollector(reg, path="/tmp/prometheus_metrics")
# → 能收集到 job_tokens / llm_requests
# 请求 http://127.0.0.1:9101/metrics → job_tokens_total{...} 150.0
```

结果：`MultiProcessCollector` 与 9101 都能读到 `job_tokens` → **聚合链路正常**。❌ 排除。

### 环 ③：白名单是否放行？（根因之一）

检查 `deploy/prometheus/prometheus.yml` 的 `metric_relabel_configs`：

```
regex: "(research_jobs_total|...|llm_usage_missing_total|process_.*)"
                                                ↑ 白名单到此为止
```

**`job_tokens_total` / `job_tool_calls_total` / `job_cost_usd_total` 不在白名单里**！
Prometheus 抓取时 `action: keep` 直接丢弃 → 查询空。✅ **根因定位**。

## 3. 根因（3 个叠加，缺一不可）

| # | 根因 | 说明 |
|---|---|---|
| ① | **白名单漏 `job_*`** | WS3 加新指标时只改了 `metrics.py`，忘了同步 `prometheus.yml` 的 relabel 白名单 |
| ② | **Celery fork 下 `os.environ.setdefault` 不可靠** | worker.py 顶部 `setdefault("PROMETHEUS_MULTIPROC_DIR", ...)` 在手动/标准 fork 下有效，但真实 Celery(billiard) 子进程不写 `.db`（反复验证：手动 import worker 后 `.inc()` 写 `.db`，真实任务后 `.db` 目录空）→ 改为 compose 真实容器 env |
| ③ | **Grafana 用 `rate(一次性 counter[5m])`** | `job_*` 是任务结束时一次性写入的 counter，`rate` 只在前 5 分钟非零、之后归零 → 面板"看起来没数据"→ 改累计值 `sum(...)` |

> 关键：根因 ①③ 让**Prometheus 侧**查不到；根因 ② 让 **Worker 侧根本没写 .db**。
> 三者叠加，从 worker 打点 → Prometheus 存储 → Grafana 展示整条链都"假空"。

## 4. 修复（三处 + 验证）

```diff
# ① prometheus.yml：两个 job 的白名单补 WS3 指标
- ...|llm_usage_missing_total|process_.*)"
+ ...|llm_usage_missing_total|job_tokens_total|job_tool_calls_total|job_cost_usd_total|process_.*)"

# ② compose.yml worker：PROMETHEUS_MULTIPROC_DIR 用真实容器 env（非仅 setdefault）
+ environment:
+   PROMETHEUS_MULTIPROC_DIR: /tmp/prometheus_metrics

# ③ research.json WS3 面板：rate → 累计值
- "sum by (profile, mode) (rate(job_cost_usd_total[5m]))"
+ "sum by (profile, mode) (job_cost_usd_total)"
```

**验证**（真实 AMZN 任务后）：
- `job_tokens_total{profile="deep",mode="annual_deep",type="total"} = 22764` ✅
- `job_cost_usd_total{...status="partial_ready_for_final_writer"} = 0.00424704` ✅（`14534/1e6×0.14 + 7901/1e6×0.28`）
- `llm_requests_total{model="deepseek-v4-flash",role="writer"} = 6`、`role="analysis" = 2` ✅

## 5. 预防与可复用排查步骤

### 加新指标的 4 步清单（最容易漏第 4 步）

1. `metrics.py` 定义指标对象；
2. `metrics_events.py` 加打点函数（`_safe_inc`/`_safe_obs`）；
3. 业务代码调用打点函数；
4. **`prometheus.yml` 两个 job 的白名单 regex 补指标名** ← 本次事故点。

### 排障速查表

| 症状 | 检查顺序 |
|---|---|
| Prometheus 查不到某指标 | ① 直接请求 `worker:9101/metrics` / `api:8001/metrics` 有没有 → ② 有则查 prometheus.yml 白名单 → ③ 没有则查打点是否触发（`metrics_inc_failed` 日志） |
| Worker 多进程指标不更新 | `/tmp/prometheus_metrics` 是否有 `.db`；`PROMETHEUS_MULTIPROC_DIR` 是否为真实 env（compose 传入，非仅 setdefault） |
| Grafana 面板空但 Prometheus 有 | 面板用 `rate`/`increase` 是否对一次性 counter 不适用；切时间范围 |

## 6. 时间线（真实排查日志片段）

```
1. 症状: job_tokens_total / llm_requests_total 查询空
2. 环①: 直接调 _record_job_perf_metrics → .inc() 无异常       （排除打点）
3. 环②: MultiProcessCollector 读到 job_tokens；9101 有值        （排除聚合）
4. 中间发现: 真实任务后 /tmp/prometheus_metrics 为空 .db
5. 深度验证: os.fork / multiprocessing / billiard 子进程都写 .db（排除 prometheus_client）
6. 根因②: /proc/1/environ 无 PROMETHEUS_MULTIPROC_DIR → setdefault 在 Celery 下不可靠
7. 修复②: compose 传真实 env → 任务后出现 counter_{pid}.db ✅
8. 环③: prometheus.yml 白名单漏 job_* → 补上
9. 根因③: Grafana rate(一次性 counter[5m]) 归零 → 改 sum(...)
10. 终验: job_tokens_total=22764, job_cost_usd_total=$0.00424704, llm 8 次 ✅
```

## 7. 教训

- **观测链路是"打点 + 聚合 + 白名单 + 展示"四层**，每层都可能让数据"假空"；
- 加指标、加面板都要**更新配套配置**（白名单、PromQL 语义）；
- Celery prefork 下的环境变量用**真实容器 env** 而不是进程内 `os.environ` 修改；
- 一次性写入的 counter 不适合 `rate`，用 `sum`/`increase`。
