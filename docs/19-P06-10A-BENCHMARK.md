# P06-10A：Phase 6 基准框架与 10 次校准记录

## 1. 目标

建立严格、可重复、可归因的 P06-10 基准框架，并完成 10 次 fake 校准。
**未执行 100 次正式基准**（需用户批准后执行）。

## 2. 基准口径定义

### 主基准任务

- 总数：100（正式）/ 10（校准）；fast 50% / deep 50%
- **主成功率分母 = 主任务总数**（不含任何控制任务）
- success：最终状态 succeeded 且规定工件完整
- failure：failed、cancelled、超时未终态、缺少必需工件

### 控制场景（单独统计，不进入主成功率）

- 用户取消（本轮实现）；幂等复用/冲突/非法请求/故障注入 by 框架预留

### 成功率区分

1. **workflow_success_rate**：fake 模式下 API/Redis/Celery/Worker/DB/Flow 全链成功率
   —— 本轮校准验证的指标；
2. **live_agent_success_rate**：真实 SEC/Serper/LLM 下的 Agent 成功率
   —— 由后续 live 基准另行评估，本轮禁止宣称。

## 3. 成功判定（每个主任务必须全部满足）

| # | 判定 | 实现位置 |
|---|---|---|
| 1 | API 最终状态 succeeded | `_run_single` checks["api_status_succeeded"] |
| 2 | current_step = None | checks["current_step_null"] |
| 3 | 无 running 步骤 | checks["no_running_step"] |
| 4 | 必需步骤 00-07 终态正确 | checks["required_steps_terminal"] |
| 5 | 08_report.md 存在且非空 | checks["08_report_md_exists_nonempty"] |
| 6 | 09_report.pdf 存在 | checks["09_report_pdf_exists"] |
| 7 | PDF 文件头 %PDF- | checks["pdf_header"] |
| 8 | 工件 API 可下载（200） | checks["artifacts_downloadable"] |
| 9 | 无未脱敏错误信息 | checks["no_unredacted_error"] |
| 10 | 任务级 timeout 内完成 | checks["within_timeout"] |

API/数据库最终状态是成功率的事实来源；Prometheus 只用于旁证（前后快照差异）。

## 4. 产物文件（scripts/run_phase6_benchmark.py）

- CLI：`--jobs --fast-ratio --concurrency --timeout-seconds --poll-interval --seed --api-base --output-dir`
- 默认 FLOW_MODE=fake；live 立即 fail-fast（含真实 key 检查）
- 固定随机种子（默认 7）；最大并发建议 4
- 加载输出到 `evals/runs/<benchmark_run_id>/`：
  config.json / jobs.jsonl / summary.json / failures.jsonl /
  metrics_snapshot_before.json / metrics_snapshot_after.json / report.md
- benchmark_run_id 只用于报告与文件，不作为 Prometheus label
- 分位数（P50/P90/P95/P99）从 jobs.jsonl 原始耗时计算

## 5. 环境隔离（deploy/compose.benchmark.yml）

- project：`invest-research-benchmark`
- FLOW_MODE=fake（写死）；LLM/SERPER key 只用占位符
- 端口避开 live（8000）与 obs-smoke（18000）：api=18080、prometheus=19091
- 独立 volume（*_benchmark）；不删主项目 down -v
- 含 Prometheus 19091 用于校准前后快照旁证

## 6. 10 次校准结果（2026-08-18 ✅ PASS）

| 指标 | 值 |
|---|---|
| benchmark_run_id | `7b17ca17a109` |
| main_jobs_total | 10（fast=5, deep=5） |
| succeeded / failed / timeout | 10 / 0 / 0 |
| workflow_success_rate | 100.00% |
| fast_success_rate / deep_success_rate | 1.0 / 1.0 |
| duration avg / min / max (s) | 0.343 / 0.321 / 0.424 |
| P50 / P90 / P95 / P99 (s) | 0.334 / 0.362 / 0.393 / 0.418 |
| strict_gt_95_percent | True |
| control_cancel_ok | True |
| jobs.jsonl 行数 | 10（与 summary 分母一致） |
| 缺工件任务 | 0 |
| report.md 密钥泄露 | 无 |

**外部调用核验**：worker 容器日志内 sec.gov / serper.dev / chat/completions 匹配数 = 0，
确认无真实外部调用（live_agent_success_rate 未评估，禁止用本轮数据宣传 Agent 成功率）。

## 7. 测试与静态检查

- `tests/test_phase6_benchmark.py`：14 passed（分母隔离、96/100 vs 95/100、cancelled
  不算成功、缺 PDF 不算成功、timeout 归因、fast/deep 分组、分位数、错误分类、
  报告不泄露密钥、live fail-fast、真实 key fail-fast + 占位符允许、seed 可复现）
- Ruff：All checks passed
- mypy src：115 source files no issues

## 8. 已知限制与下一步

- 本轮校准未做 Prometheus 前后快照的实际查询（before/after 为空 dict 占位）；
  正式 100 次基准前应接入 19091 快照查询。
- 控制场景目前只有"用户取消"；幂等复用/冲突/非法请求/故障注入留给正式基准或后续任务。
- P06-10 正式 100 次基准**未执行**；需用户批准后运行
  `--jobs 100 --fast-ratio 0.5 --concurrency 4`。