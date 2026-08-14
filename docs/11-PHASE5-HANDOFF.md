# Phase 5 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限时，把已完成工作、下一任务、测试结果、Git 状态完整记录下来，
> 供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`
- **已提交 Phase 5 任务**：P05-01 ✅、P05-02 ✅、P05-03 ✅、P05-03A ✅、P05-03B ✅、P05-04 ✅、P05-05 ✅
- **下一个待执行任务**：**P05-05（结构化日志与敏感字段脱敏 / structlog config + tests）**
- **已补充到路线图的子任务**：P05-03A、P05-12A（见 docs/05-DEVELOPMENT-ROADMAP.md）

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| P05-01 ✅ | 通用 retry policy（Tenacity：白名单错误 + 指数退避 + 抖动 + 可注入 sleep） | 14 passed | `ca25c08` |
| P05-02 ✅ | 尊重 `Retry-After` 并动态降速（秒/HTTP-date 双格式 + Provider 注入） | 26 passed | `a5591da` |
| P05-03 ✅ | 步骤 lease 与 stale recovery（StaleStepSnapshot / StepLeaseStore 端口 / StaleRecoveryService） | 6 passed | `98e56ca` |
| P05-03A ✅ | Worker 启动自动 stale recovery + RecoveryCounter 计数 | 9 passed | `04322f5` |
| P05-04 ✅ | 输入 hash 与下游失效（compute_input_hash / should_recompute / VersioningService） | 11 passed | `6fbbe6e` |

**P05-01~P05-04 后回归**：P05-01/02（test_retry_policy + test_retry_after）26 passed；
P05-03/03A（test_recovery + test_recovery_bootstrap）9 passed；P05-04（test_versioning）11 passed。
全部任务后 `ruff check` + `mypy`（strict）均通过。

## 3. 完成任务的产物文件

```
src/invest_research/infrastructure/retry.py           # P05-01/02 核心
src/invest_research/application/recovery.py            # P05-03/03A 核心
src/invest_research/infrastructure/queue/recovery_bootstrap.py  # P05-03A
src/invest_research/application/versioning.py           # P05-04 核心
tests/test_retry_policy.py                            # 14 tests
tests/test_retry_after.py                             # 12 tests
tests/test_recovery.py                                # 6 tests
tests/test_recovery_bootstrap.py                      # 3 tests
tests/test_versioning.py                              # 11 tests
pyproject.toml  (新增 tenacity)
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P05-01/02/03/03A/04 已标 ✅；P05-03A/P05-12A 已补充进表格
- `docs/09-LEARNING-LOG.md`：P05-01 ~ P05-04 学习条目已添加（含检查问题，未附答案）

## 5. 下一任务 P05-05 的准备工作

**任务**：结构化日志与敏感字段脱敏（structlog config + tests）
**路线验收**：key/header/cookie 不出现在日志
**关键背景**（对齐 docs/04 §8.1）：
- 日志字段要求：`timestamp`、`level`、`event`、`job_id`、`step_name`、`tool_name`、
  `attempt`、`trace_id`、`duration_ms`、`error_code`。
- 禁止记录：密钥、完整 Authorization、未截断原文。
- 项目已有 `logging` 用法：`recovery_bootstrap.py` 用 `logger.info("startup_recovery_done", extra=...)`。

**建议实现**（P05-05 最小切片）：
1. 新增 `src/invest_research/infrastructure/observability/logging.py`：
   - `sanitize(value)`：把 key/Authorization/密码等替换为 `***`；
   - `setup_structlog(...)`：配置 structlog（字段脱敏 + 标准字段）；
   - 提供 `mask_secrets(text)` 等纯函数测试。
2. 新增 `tests/test_observability.py`（或 test_logging.py）：
   - key 值、Authorization 头、Cookie 不出现在渲染后日志；
   - 普通字段（job_id/step_name）保持可见。
3. 运行 `uv run pytest tests/test_*.py -q`（新测试）+ `ruff check` + `mypy`
4. 更新路线图✅ + 学习日志，commit `feat(p05-05): add structlog with secret redaction`
   （如未装 structlog 则 `uv add structlog`）

**P05-06~P05-15 路线（供规划上下文）**：P05-06 Prometheus /metrics；
P05-07 OpenTelemetry；P05-08 Grafana dashboard；P05-09 timeout/429/5xx 故障注入；
P05-10 坏 PDF/非法 LLM JSON；P05-11 DB/工件写入失败；P05-12 SEC fixture + sanitizer；
P05-12A FLOW_MODE=fake/live；P05-13 opt-in E2E（需授权）；P05-14 20 公司 × 5 场景数据集；
P05-15 benchmark runner。

## 6. 已知风险与注意事项

1. **write_to_file 中文路径截断 bug**：在含中文的目录（本工作区根目录含中文）下，`write_to_file` 偶尔出现路径截断（误创建 `src/invest_res`、`tests/test`）。
   **应对**：文件写入异常时先用 `git status` / `py -c "import pathlib; print(pathlib.Path(...).exists())"` 验证；必要时重试 write_to_file 或用 `replace_in_file`（需先 read_file）。
2. **tenacity 新版 API**：`Retrying` 实例是可调用对象（`retrying(fn)`），新版无 `.call()` 方法。
3. **Ruff I001/W292/E501**：新文件提交前先 `uv run ruff check --fix <files>`；行长 >100 需换行。
4. **P05-05 structlog**：如未安装需 `uv add structlog`；脱敏必须覆盖 key/Authorization/Cookie。
5. **P05-12 需要真实 SEC 数据**：如无有效 SEC 联系邮箱，暂停外部录制，只完成离线 fixture 框架与脱敏器。
6. **P05-13 需要真实千问/搜索 API**：不得擅自产生费用；无授权时保持未完成状态并继续 P05-14/15。
7. **不 push / 不部署 / 不触发 Docker 构建**（Phase 5 全部完成后再确认）。

## 7. Git 状态（本快照）

P05-04 完成后工作区已清空（所有变更已 commit）。最新 commit：`6fbbe6e`。

## 8. 新窗口继续短提示词

> 【Phase 5 继续（安全交接）】请读取 `docs/11-PHASE5-HANDOFF.md` 和
> `docs/05-DEVELOPMENT-ROADMAP.md`，确认当前在 P05-05。
> 当前分支 `agent/m2-deterministic-tools`，已完成 P05-01/02/03/03A/04（均已 commit），
> 无未提交修改。按 P05 自动连续执行规则，从 P05-05 开始继续，
> 每任务完成 → 测试 → ruff/mypy → 标✅ → 更新学习日志 → commit → 自动继续
> → 直到 P05-15 和 Phase 5 最终检查；P05-12 真实 SEC 与 P05-13 真实模型
> 未经授权不得执行 live 调用。上下文接近上限时更新本文件并停止。