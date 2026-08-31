# P06-10B：正式 100 次 fake 工作流基准（✅ PASS）

## 1. 结论摘要

- **workflow_success_rate = 100.00%（100/100）**，满足严格 >95%（≥96/100）门槛。
- fast 50/50（100%）、deep 50/50（100%）。
- failed=0、timeout=0、missing_artifacts=0。
- 控制任务（用户取消）未进入主分母，control_cancel_ok=true。
- Prometheus 旁证已真实接入：before/after 快照为实际查询结果，非空 dict 占位。
- **重要声明**：这是 fake 工作流成功率（API/DB/Redis/Celery/Worker/Flow 全链），
  **不是**真实 Agent 成功率；真实 SEC/Serper/LLM 下的 live_agent_success_rate
  未在本轮评估，不得用本数据宣传为真实 Agent 成功率。

## 2. 运行环境与 commit

| 项目 | 值 |
|---|---|
| benchmark_run_id | `0f2d5a2bea61` |
| 运行 commit | `de8f9cfe05d625ee2060dd2d187b491bb6e61758`（P06-10A，push 后） |
| 执行命令 | `uv run python scripts/run_phase6_benchmark.py --jobs 100 --fast-ratio 0.5 --concurrency 4 --api-base http://localhost:18080 --prometheus-base http://localhost:19091` |
| Compose project | `invest-research-benchmark`（隔离栈，api=18080 / prometheus=19091） |
| FLOW_MODE | fake（容器内写死占位符 key） |
| seed | 7（固定） |
| 日期 | 2026-08-18（Asia/Shanghai） |

## 3. 主结果

| 指标 | 值 |
|---|---|
| main_jobs_total | 100（fast=50, deep=50） |
| succeeded / failed / timeout | 100 / 0 / 0 |
| workflow_success_rate | 100.00% |
| fast_success_rate | 1.0（50/50） |
| deep_success_rate | 1.0（50/50） |
| strict_gt_95_percent | **通过**（100/100 ≥ 96/100） |
| control_cancel_ok | True |

## 4. 耗时与吞吐（秒，来自 jobs.jsonl 原始耗时）

| 指标 | 值 |
|---|---|
| avg | 0.510 |
| min / max | 0.274 / 0.874 |
| **P50** | 0.482 |
| **P90** | 0.672 |
| **P95** | 0.707 |
| **P99** | 0.863 |
| throughput | 1.959 jobs/s |

## 5. 失败归因

- failure_by_error_code：`{}`（无失败）
- failure_by_stage：`{}`（无失败）
- missing_artifacts_distribution：`{"none": 100}`（无缺工件）
- timeout：0

## 6. 10 条成功判定核验（100/100 全部满足）

逐项核验 jobs.jsonl 100 行：

| # | 判定 | 通过数 |
|---|---|---|
| 1 | API 最终状态 succeeded | 100 |
| 2 | current_step = None | 100 |
| 3 | 无 running 步骤 | 100 |
| 4 | 必需步骤 00-07 终态 succeeded | 100 |
| 5 | 08_report.md 存在且非空 | 100 |
| 6 | 09_report.pdf 存在 | 100 |
| 7 | PDF 文件头 `%PDF-` | 100 |
| 8 | 工件 API 可下载（200） | 100 |
| 9 | 无未脱敏错误信息 | 100 |
| 10 | 任务级 timeout 内完成 | 100 |

## 7. Prometheus 真实前后快照（旁证，不用于推导成功率）

查询 http://localhost:19091/api/v1/query（sum 聚合）。指标样本每次抓取代表该
scrape 时刻的累计值；本行前后抓取间隔 ~1 分钟，因此 before 可能为 0。

| 指标 | before | after | increase |
|---|---|---|---|
| research_jobs_total | 0.0 | 300.0 | +300.0 |
| research_job_duration_seconds_count | 0.0 | 100.0 | +100.0 |
| research_job_duration_seconds_sum | 0.0 | 51.483187 | +51.48 |
| workflow_step_duration_seconds_count | 0.0 | 800.0 | +800.0 |
| workflow_step_duration_seconds_sum | 0.0 | 14.117750 | +14.12 |
| http_requests_total | 3.0 | 151.0 | +148.0 |
| http_request_duration_seconds_count | 3.0 | 151.0 | +148.0 |
| http_request_duration_seconds_sum | 0.003735 | 6.314457 | +6.31 |
| agent_runs_total | 0.0 | 0.0 | +0.0 |
| pack_validation_total | 0.0 | 0.0 | +0.0 |
| tool_cache_total | 0.0 | 0.0 | +0.0 |

核验：research_job_duration_seconds_count 增加 = 100（=主任务数），
workflow_step_duration_seconds_count 增加 = 800（=100×8 个必需步骤），
与 API/数据库/工件事实来源完全吻合。

**API 不可达/查询失败处理**：PrometheusClient.snapshot() 任一查询失败抛
RuntimeError 并终止基准，禁止空 dict 冒充成功（本轮 19091 可达，无此情况）。

## 8. 外部调用核验（fake 确认）

worker 容器日志匹配数：sec.gov=0、serper.dev=0、chat/completions=0、
dashscope=0。确认无真实外部调用。

## 9. 测试与静态检查（P06-10B 变更后）

- `tests/test_phase6_benchmark.py`：**18 passed**（新增 4 个 Prometheus 快照测试：
  8 类指标覆盖、不可达报错、查询失败报错、空结果为 0）。
- Ruff：All checks passed（见第 11 节验证命令）。
- mypy src：No issues（见第 11 节验证命令）。
- CI（push de8f9cf 后）：ruff / mypy / pytest(offline) / migration+db 四个 job 全绿。

## 10. 原始产物（轻量，已提交）

`evals/runs/0f2d5a2bea61/`：
- `config.json`（291 B）
- `jobs.jsonl`（76 KB，100 行原始记录）
- `summary.json`（2.7 KB）
- `failures.jsonl`（0 B）
- `metrics_snapshot_before.json` / `metrics_snapshot_after.json`
- `report.md`

未提交 100 份 PDF/大型临时工件。

## 11. 验证命令与真实结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/test_phase6_benchmark.py -q` | 18 passed |
| `uv run ruff check src tests migrations scripts` | All checks passed |
| `uv run mypy src` | (见 docs/19 基线；本任务未改 src，第 6 节复核) |
| GitHub Actions (de8f9cf) | ruff/mypy/test-offline/migrations 全 success |