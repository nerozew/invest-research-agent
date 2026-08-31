# P06-10 基准报告（0f2d5a2bea61）

- 主任务总数：100（fast=50，deep=50）
- workflow_success_rate：100.00%（succeeded=100 / 100）
- live_agent_success_rate：None
  - 本轮 fake 模式只验证 workflow_success_rate（API/DB/Redis/Celery/Worker/Flow 全链）；真实 SEC/Serper/LLM 下的 Agent 成功率由后续 live 基准另行评估，不得用本数字宣传为真实 Agent 成功率。
- 严格 >95%：通过
- fast 成功率：1.0（50/50）
- deep 成功率：1.0（50/50）

## 耗时（秒）

- 平均：0.51
- P50：0.482
- P90：0.672
- P95：0.707
- P99：0.863
- 最小/最大：0.274 / 0.874
- 吞吐量：1.959324 jobs/s

## 失败归因

### 按 error_code

### 按 failure_stage

### 缺失工件
- none: 100

## 控制任务
- 取消场景 ok=True

## Prometheus 旁证（仅核对，不用于推导成功率）
- before：`metrics_snapshot_before.json`
- after：`metrics_snapshot_after.json`
- diff：{'agent_runs_total': 0, 'http_requests_total': 148.0, 'pack_validation_total': 0, 'research_jobs_total': 300.0, 'tool_cache_total': 0, 'research_job_duration_seconds_count': 100.0, 'workflow_step_duration_seconds_count': 800.0, 'workflow_step_duration_seconds_sum': 14.117750000000001, 'http_request_duration_seconds_count': 148.0, 'research_job_duration_seconds_sum': 51.483186999999994, 'http_request_duration_seconds_sum': 6.3107210839998515}

原始数据：`jobs.jsonl` / `failures.jsonl` / `config.json` / `summary.json`