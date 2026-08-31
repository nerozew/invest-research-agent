# P06-11：10 家公司 fast/deep 真实效率对照实验（Live Benchmark 评测工具）

> 状态：**评测工具已完成**（脚本/数据集/离线测试/文档）；真实付费 live benchmark **尚未执行**，
> P06-11 在路线图中保持未标记 ✅，直到用户真实执行 paired benchmark 并产生完整原始记录。

## 1. 目标

实现一套可重复、可中断恢复、有预算保护、**默认绝不产生付费调用**的本地 live benchmark，
用于对照 fast / deep 两种 research profile 在真实 SEC / Serper / LLM 下的效率与质量。

本工具只允许**增加评测、数据采集、报告与必要的无侵入性读取**；不修改业务流程、Prompt、
Agent 行为或质量门禁。

## 2. 文件清单

| 文件 | 说明 |
|---|---|
| `scripts/run_live_portfolio_benchmark.py` | 主脚本（CLI，约 2200 行） |
| `evals/live_dataset.json` | 固定 10 家公司数据集（AAPL/MSFT/AMZN/JPM/JNJ/WMT/XOM/BA/KO/TSLA） |
| `tests/test_live_portfolio_benchmark.py` | 52 个离线测试（fake HTTP/Prometheus/文件系统，不联网） |
| `docs/23-P06-11-LIVE-BENCHMARK.md` | 本文档 |

## 3. 安全边界

1. **默认 dry-run**：不联网、不调用 SEC/Serper/LLM、不写任何运行目录。
2. 真正运行必须同时满足：
   - 显式提供 `--confirm-live`（且未给 `--dry-run`）；
   - 根目录 `.env` 的 `FLOW_MODE=live`（通过 `Settings` 校验）；
   - `LLM_API_KEY` / `SERPER_API_KEY` 非占位符（拒绝 fake/placeholder/secret/过短 sk-）；
   - API `/health`、`/readiness`、Prometheus `/-/healthy` 预检通过；
   - Prometheus 不可达时 **fail-fast**（绝不空 dict 伪装成功）。
3. 不读取/打印/写入 API Key、完整 Prompt、完整模型响应、推理内容；日志、异常、JSONL、
   报告一律脱敏（`_redact`）。
4. **不执行** `docker compose down -v`；不删除现有 volume/数据库/工件/`evals/runs`。
5. 预算保护参数：`--max-jobs`、`--max-failures`（默认 2，连续或累计达到即停）、
   `--timeout-seconds`、`--cooldown-seconds`、`--max-total-tokens`（仅基于真实 usage，
   best-effort）、`--pricing-file`（可选）。

## 4. 三个评测套件

| suite | 内容 | 用途 |
|---|---|---|
| `smoke` | 前 2 家公司 × fast/deep = 4 个 job，concurrency=1 | 验证真实配置、输出契约、Token 量级；结果不直接作为简历统计 |
| `paired` | 10 家 × fast/deep × repeats（正式推荐 3 → 60 个 job），concurrency 固定 1 | 主要 fast/deep 配对效率实验；fast-first/deep-first 按公司奇偶平衡，下一 repetition 交换 |
| `load` | `--concurrency-levels 1,2,4`，每级 ≥8 个任务，默认 fast | 并发容量实验（与 paired 分离）；Worker concurrency=1 时报告明确标注"只测到排队能力" |

配对的 pair_id = `live-<ticker>-r<rep>`；Idempotency-Key 稳定包含
`run_id/case/profile/repetition`。

## 5. 三个成功口径（绝不混用）

| 口径 | 判定要点 |
|---|---|
| `technical_success` | API succeeded + current_step=None + 无 running 步骤 + 00–07 步骤终态正确 + 00–09 工件齐 + MD 非空 + PDF `%PDF-` + 工件可下载 + 未超时 + 无敏感泄漏 |
| `live_acceptance` | technical 之外：manifest 证明真实 SEC/Serper/LLM 调用 + recommendation ∈ {published, publish_partial} + citation_keys 非空 + ≥1 个带 locator 的 SEC 官方来源 + 数据日期不晚于 as_of_date + 5 个 pack 可解析 |
| `full_quality_pass` | live_acceptance 之外：all_passed=true + recommendation=published + analysis_completeness=complete |

`publish_partial` 永远不算 full quality。

## 6. 统计口径

- 主指标：三个成功率 + 明确的分子/分母 + **Wilson 95% 区间**。
- 延迟：queue/execution/E2E 的 avg/min/max/P50/P90/P95/P99；小样本（<30）P95/P99
  标注"不稳定，仅作观察"。
- Token/成本：input/output/cached_input/total（缺失写 null，**绝不用字符数估算**）；
  usage 完整率 <95% 时**不生成**"节省 XX% Token/费用"简历结论；
  只有提供带日期+模型名的 `--pricing-file` 才计算 `estimated_cost_usd`，
  否则输出 null（代码不硬编码模型价格）。
- Prometheus：运行前后保存 before/after 快照（保留 label 维度），算 delta；
  检测 Counter 重置（after<before）并告警；历史累计 Counter 不作为本轮结果。

## 7. 输出目录

每次运行写入 `evals/live_runs/<run_id>/`：
`config.json`、`environment.json`（脱敏，不含密钥）、`dataset_snapshot.json`、
`jobs.jsonl`（每任务完整原始记录）、`failures.jsonl`（只写失败任务）、
`metrics_snapshot_before/after.json`、`metrics_diff.json`、`summary.json`、
`paired_comparison.csv`、`company_breakdown.csv`、`report.md`、`claim_candidates.json`。

`report.md` 明确区分：既有 fake workflow benchmark（P06-10）、本次 live agent benchmark、
paired 效率结果、load 并发结果、样本量、测试环境、已知限制、可以写进简历的事实、
当前证据不足不能写进简历的说法。

## 8. 恢复与幂等

- `--resume <run_id>`：读取既有 `jobs.jsonl`，跳过已有终态且记录完整的
  (case_id, profile, ticker, repetition)，不重复付费执行。
- 中途 Ctrl+C：`KeyboardInterrupt` 安全保存已完成结果 + 中间汇总 `summary_partial.json`。
- 相同 run_id/case/profile/repetition 不重复提交（幂等键稳定）。
- resume 后汇总不重复计数（既有记录 + 新记录拼接）。
- **不覆盖旧 run**（run_id 唯一；`--output-dir` 可覆盖默认路径）。

## 9. 命令示例

```bash
# dry-run（默认安全，绝不联网）
uv run python scripts/run_live_portfolio_benchmark.py --suite smoke --dry-run

# smoke（真实 live，需要 FLOW_MODE=live + 真实密钥 + API/Prometheus 就绪）
uv run python scripts/run_live_portfolio_benchmark.py \
  --suite smoke --api-base http://localhost:8000 \
  --prometheus-base http://localhost:9090 --confirm-live

# paired（正式推荐）
uv run python scripts/run_live_portfolio_benchmark.py \
  --suite paired --repeats 3 --concurrency 1 --seed 7 \
  --api-base http://localhost:8000 \
  --prometheus-base http://localhost:9090 --confirm-live

# load
uv run python scripts/run_live_portfolio_benchmark.py \
  --suite load --profile fast --concurrency-levels 1,2,4 \
  --jobs-per-level 8 --api-base http://localhost:8000 \
  --prometheus-base http://localhost:9090 --confirm-live
```

### 9.1 排除 AAPL/MSFT 的 20 个配对样本方案

`evals/live_dataset_no_aapl_msft.json` 固定 10 家公司：AMZN、JPM、JNJ、WMT、
BA、KO、TSLA、NVDA、HD、CAT。`--repeats 2` 形成 20 个完整 fast/deep pair，
共 40 个 Job。XOM 因当前 SEC 主体 CIK 变化可能干扰固定 2025 as-of 的历史比较，
本组不纳入。

本方案使用显式且写入 `config.json` 的候选结论样本门槛：2 次重复、20 个完整配对、
10 家不同公司。其他质量条件不变；最终简历必须注明样本范围，不得表述为普遍结论。

```powershell
$dataset = "evals\live_dataset_no_aapl_msft.json"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$pairedRun = "p0611-resume20-$stamp"

# 免费检查：应显示 40 个任务；不联网、不写运行目录
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite paired --dataset $dataset --repeats 2 --seed 7 `
  --claim-min-repeats 2 --claim-min-complete-pairs 20 --claim-min-companies 10 `
  --run-id $pairedRun --dry-run

# 先只执行前 2 个 pair（4 jobs）作为付费 canary
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite paired --dataset $dataset --repeats 2 --seed 7 --max-jobs 4 `
  --claim-min-repeats 2 --claim-min-complete-pairs 20 --claim-min-companies 10 `
  --run-id $pairedRun --api-base http://localhost:8000 `
  --prometheus-base http://localhost:9090 --confirm-live

# canary 正常后恢复同一 run；跳过前 4 个终态任务，完成余下任务
.venv\Scripts\python.exe scripts\run_live_portfolio_benchmark.py `
  --suite paired --dataset $dataset --repeats 2 --seed 7 `
  --claim-min-repeats 2 --claim-min-complete-pairs 20 --claim-min-companies 10 `
  --resume $pairedRun --api-base http://localhost:8000 `
  --prometheus-base http://localhost:9090 --confirm-live
```

## 10. 测试与静态检查（本任务已完成并实际执行）

| 命令 | 结果 |
|---|---|
| `.venv python -m pytest tests/test_live_portfolio_benchmark.py -q` | **46 passed** |
| `.venv ruff check scripts/run_live_portfolio_benchmark.py tests/test_live_portfolio_benchmark.py` | **All checks passed** |
| `uv run python scripts/run_live_portfolio_benchmark.py --suite smoke --dry-run` | dry-run 正确输出 4 个计划任务 + 稳定幂等键，不联网 |

说明：
- 本机 `uv run` 出现 "uv trampoline failed to canonicalize script path" 环境错误，
  因此用 `.venv\Scripts\python.exe -m pytest` 与 `.venv\Scripts\ruff.exe` 执行验证；
  这是本机 uv trampoline 的已知问题，不是代码错误。
- `mypy` 只检查 `src`（pyproject.toml 的 `[tool.mypy]` 无 scripts 目标），
  `scripts/` 与 `tests/` 不在 mypy 检查范围；本任务未修改 `src/`，故不重跑 `mypy src`。

## 11. 已知限制

- 409 idempotency conflict 响应不含 job_id（API 契约限制），无法恢复冲突 job；按
  `IDEMPOTENCY_CONFLICT` 记录并跳过该任务。
- Token/usage 依赖 `manifest.performance.llm_usage`；若 manifest 不含 usage，
  记录为 `token_usage_complete=false` 且 Token 字段为 null（不伪造）。
- Prometheus 只能作为聚合旁证；并发运行下无法把单 job Token 精确归因到任务时，
  单任务 Token 写 null，只提供 run-level/profile-level 聚合。
- smoke 结果不得直接作为最终简历统计。
- load suite 的 concurrency=2/4 在 Worker concurrency=1 时主要测到排队能力。

## 12. 真实运行顺序（用户自行执行，本工具不自动发起）

1. 确认栈为 `FLOW_MODE=live`（API/Worker/Prometheus 已启动，`/readiness` 与
   Prometheus `/-/healthy` 可达，`.env` 为真实密钥）。
2. 先跑 `--suite smoke --confirm-live`（4 个 job），核对输出契约、Token usage 与费用量级。
3. 确认 smoke 稳定后，跑 `--suite paired --repeats 3 --seed 7 --confirm-live`
   （10×2×3 = 60 个 live job，concurrency=1）。
4. 可选 `--suite load --profile fast --confirm-live` 并发容量实验。
5. 检查 `evals/live_runs/<run_id>/` 输出；只有满足第五/六节全部条件时，
   才可把 claim_candidates.json 中 claim_eligible=true 的结论用于简历。
6. 完成真实 paired 并产生完整原始记录后，才按路线图规则把 P06-11 标记 ✅。
