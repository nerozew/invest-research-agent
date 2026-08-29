# P07-11：annual_deep / legacy 可复现基准评测

> 状态：**评测工具已完成**；真实 live canary 尚未执行，路线图不得因此标记完成。

## 目的

在同一公司、同一 `as_of_date`、同一 `deep` 档位和仅 `10-K` 的范围内，比较
`legacy` 与 `annual_deep` 的端到端耗时、年度 DAG 关键路径、成功率、证据验收和安全降级。
工具默认 dry-run，绝不自动发起 SEC、Serper 或 LLM 调用。

## 固定首轮设计

- 数据集：`evals/annual_benchmark_dataset.json`，AMZN、JPM。
- 重复：每家公司 2 次；每次产生 `legacy` 与 `annual_deep` 两个串行任务，共 8 个 Job。
- 控制：固定 `research_profile=deep`、`requested_forms=["10-K"]`；每个 pair 交替先执行模式。
- 输出：`evals/annual_benchmark_runs/<run_id>/`，包含原始记录、模式配对 CSV、公司/模式 CSV、指标快照、汇总与报告。

年度任务不使用 legacy 的 `workflow_steps` 当作验收事实：它要求年度节点图、Company Facts、Comparison Pack、章节工件、引用和限制说明齐全。关键证据缺失后的阻塞记录为安全阻塞，不能作为成功发布。

## 运行

```powershell
# 免费检查：应输出 8 个任务；不联网、不写运行目录
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite annual-paired --repeats 2 --dry-run

# 真实 canary：仅在 API、Worker、Redis、Prometheus 与真实密钥都就绪后人工执行
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite annual-paired --repeats 2 --max-failures 2 `
  --api-base http://localhost:8000 --prometheus-base http://localhost:9090 `
  --confirm-live

# 中断恢复：终态的 (case, mode, repetition) 不会重复提交
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite annual-paired --repeats 2 --resume <run_id> `
  --api-base http://localhost:8000 --prometheus-base http://localhost:9090 `
  --confirm-live
```

## 解释边界

- Token 只取模型真实 `usage`；缺失则为 `null`，完整率不足 95% 不输出 Token/费用节省结论。
- 年度 LLM Manifest 仅记录调用次数、耗时、模型和 Token 汇总；不记录 Prompt、原始 SEC 内容、密钥、job ID 或公司名到 Prometheus 标签。
- 两家公司 × 两次仅用于验证工具、并行关键路径和安全降级；`claim_candidates.json` 固定 `claim_eligible=false`。扩样并完成真实运行后，才可讨论性能结论。

## 2026-08-27 首次 live canary incident

首个 AMZN `annual_deep` 任务在进入章节执行后因年度节点关键路径计算混用
naive/aware datetime 而失败；评测轮询也因此无法读取任务详情，后续 7 个任务未提交。
该尝试只记录为运行时缺陷证据，不产生 benchmark 结果、性能结论或简历数据。修复后必须
使用新的 run id 执行完整 8 任务 canary，不能复用这次失败任务的幂等身份。
