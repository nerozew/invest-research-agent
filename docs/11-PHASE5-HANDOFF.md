# Phase 5 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限时，把已完成工作、下一任务、测试结果、Git 状态完整记录下来，
> 供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`
- **已提交 Phase 5 任务**：P05-01 ~ P05-11 全部 ✅
- **下一个待执行任务**：**P05-12（SEC Fixture 录制与脱敏 / record/replay）**
- **已补充到路线图的子任务**：P05-03A、P05-12A（见 docs/05-DEVELOPMENT-ROADMAP.md）
- **上下文快照**：P05-10/11 完成后已刷新（详见第 8 节）

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| P05-01 ✅ | 通用 retry policy | 14 passed | `ca25c08` |
| P05-02 ✅ | 尊重 `Retry-After` 并动态降速 | 26 passed | `a5591da` |
| P05-03 ✅ | 步骤 lease 与 stale recovery | 6 passed | `98e56ca` |
| P05-03A ✅ | Worker 启动自动 stale recovery + 计数 | 9 passed | `04322f5` |
| P05-03B ✅ | Transactional Outbox | outbox tests | `66ca83b` |
| P05-04 ✅ | 输入 hash 与下游失效 | 11 passed | `6fbbe6e` |
| P05-05 ✅ | 结构化日志与敏感字段脱敏 | test_observability | `9d87b0a` |
| P05-06 ✅ | Prometheus /metrics | test_metrics | `b8e3b75` |
| P05-07 ✅ | OpenTelemetry trace | test_tracing | `f830367` |
| P05-08 ✅ | Grafana dashboard | test_grafana_dashboard | `9e19f9e`、`3c543e5` |
| P05-09 ✅ | 故障注入 timeout/429/5xx | test_fault_injection | `ddfacd4` |
| P05-10 ✅ | 故障注入 坏 PDF/非法 LLM JSON | 44 passed（含回归） | `fd1a472` |
| P05-11 ✅ | 故障注入 DB/工件写入失败 | 11 passed + 54 passed 回归 | `3d24926` |

## 3. 完成任务的产物文件（P05-10/11 增量）

```
tests/test_fault_injection_pdf.py        # P05-10 损坏 PDF/降级路由（8 tests）
tests/test_fault_injection_llm.py        # P05-10 非法 LLM/guardrail/reflection（8 tests）
tests/test_fault_injection_db.py         # P05-11 Job+Outbox 事务/连接失败（4 tests）
tests/test_fault_injection_artifact.py   # P05-11 原子写各阶段故障（7 tests）
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P05-01 ~ P05-11 已标 ✅（最早未完成 = P05-12）
- `docs/09-LEARNING-LOG.md`：P05-01 ~ P05-11 学习条目已添加（含检查问题，未附答案）
- P05-10/11 学习条目各含 3 知识点 + 1 检查问题 + 已知限制

## 5. 下一任务 P05-12 的准备工作

**任务**：SEC fixture recorder 接口 + 响应脱敏器 + 离线回放测试
**授权边界**：未经确认不执行新的真实 SEC 网络录制；仓库已有合法 fixture：
- `tests/fixtures/sec_submissions_msft.json`（P02-05）
- `tests/fixtures/companyfacts_msft.json`（P02-06）
**可离线完成**：基于已有 fixture 的脱敏器、meta 记录（来源 URL/录制日期/schema 版本/checksum）、离线回放测试
**验收**：脱敏移除 Authorization/Cookie/API Key；CI 普通测试不访问真实 SEC

**P05-12A~P05-15 路线**：P05-12A FLOW_MODE=fake/live wiring；
P05-13 opt-in E2E smoke（默认 skip，需 live 授权）；P05-14 20公司×5场景 evals 数据集；
P05-15 benchmark runner（fake/fixture/live）。

## 6. 已知风险与注意事项

1. **write_to_file 中文路径截断 bug**：含中文路径下可能截断；写入异常时用 `git status`/`pathlib.Path(...).exists()` 验证，必要时重试或改 replace_in_file。
2. **Ruff W292**：新文件末尾需换行，提交前 `uv run ruff check --fix <files>`。
3. **P05-12 真实 SEC**：无授权不得录制；基于已有 fixture 完成离线部分。
4. **P05-12A live wiring**：只做 fake/mock 离线 wiring 测试，不实际调用付费模型。
5. **P05-13 真实 E2E**：调用真实千问/搜索前必须获得授权；无授权时保持"实现完成，等待受控 live run"。
6. **不 push / 不部署 / 不触发 Docker 构建 / 不进入 P06**。

## 7. Git 状态（本快照）

P05-11 完成后工作区已清空（所有变更已 commit）。最新 commit：`3d24926`。
分支 `agent/m2-deterministic-tools` 领先 origin 20 commits（未 push）。

## 8. 新窗口继续短提示词

> 【Phase 5 继续（安全交接）】请读取 `docs/11-PHASE5-HANDOFF.md` 和
> `docs/05-DEVELOPMENT-ROADMAP.md`，确认当前在 P05-12。
> 当前分支 `agent/m2-deterministic-tools`，已完成 P05-01 ~ P05-11（均已 commit，
> 最新 `3d24926`），工作区干净。按 P05 自动连续执行规则从 P05-12 开始继续；
> P05-12 未经授权不执行真实 SEC 录制，基于已有 fixture 完成离线脱敏器+回放测试；
> P05-12A 只做 fake/mock 离线 wiring；P05-13 无 live 授权保持"等待受控 live run"；
> P05-14/15 可离线完成。每任务完成 → 测试 → ruff/mypy → 标✅ → 学习日志 → commit；
> 不 push / 不部署 / 不进入 P06。上下文接近上限时更新本文件并停止。
