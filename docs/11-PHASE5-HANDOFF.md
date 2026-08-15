# Phase 5 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限时，把已完成工作、下一任务、测试结果、Git 状态完整记录下来，
> 供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`
- **已提交 Phase 5 任务**：P05-01 ~ P05-11 全部 ✅；**P05-12 ✅（真实录制完成）**；**P05-12A ✅**；**P05-12B ✅（真实 LLM/工具/FlowRunner 生产组装 + 离线验收）**
- **下一个待执行任务**：**P05-13（真实 E2E smoke，opt-in）**
- **已补充到路线图的子任务**：P05-03A、P05-12A、P05-12B（见 docs/05-DEVELOPMENT-ROADMAP.md）
- **上下文快照**：P05-12B 离线验收通过后已刷新（详见第 8 节）

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
| P05-12 ✅ | 录制一家公司 SEC 契约 fixture（真实录制 AAPL） | test_fixture_sanitizer 12 passed + 回放验证 | `da2f07d` |
| P05-12A ✅ | FLOW_MODE=fake/live 与生产 Flow wiring | test_flow_wiring 10 passed + 608 回归 | `7bcbae0` |
| P05-12B ✅ | 真实 LLM、Agent 工具和 LiveResearchFlowRunner 生产组装（离线验收通过） | `build_real_llm`/`AnyLLM`/real_tools/LiveResearchFlowRunner.run + 37 测试全绿 | 见 commit `P05-12B` |

## 3. 完成任务的产物文件（P05-10~12 增量）

```
tests/test_fault_injection_pdf.py        # P05-10（8 tests）
tests/test_fault_injection_llm.py        # P05-10（8 tests）
tests/test_fault_injection_db.py         # P05-11（4 tests）
tests/test_fault_injection_artifact.py   # P05-11（7 tests）
src/invest_research/infrastructure/fixture.py   # P05-12 脱敏器/meta/回放
tests/test_fixture_sanitizer.py          # P05-12（10 + 2 守卫 tests）
scripts/record_sec_fixture.py            # P05-12 真实 SEC 录制脚本（AAPL, as_of=2025-10-31）
scripts/verify_recorded_fixture.py       # P05-12 离线回放+脱敏验证
tests/fixtures/sec_recorded_aapl.json    # P05-12 真实录制 fixture（无敏感字段）
src/invest_research/infrastructure/flow_wiring.py  # P05-12A build_flow_runner/live runner
tests/test_flow_wiring.py                # P05-12A（10 contract tests）
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P05-01 ~ P05-11 全部 ✅；**P05-12 ✅（真实录制）**；**P05-12A ✅**；**P05-12B（新增，待实现）**
- `docs/09-LEARNING-LOG.md`：P05-01 ~ P05-12 + P05-12A 学习条目已添加（含检查问题，未附答案）

## 5. 下一任务 P05-12B 的准备工作

**P05-12B 已完成（离线验收）**：真实 LLM builder（CrewAI 1.6.1 实测 `LLM(model, base_url, api_key, temperature, timeout)`）；统一 `AnyLLM` 接口；真实工具注入（real_tools.py，Research=6 工具白名单）；`LiveResearchFlowRunner.run` 完整控制流（Crew→pack→质量门禁→受控反思→manifest→工件落盘，可注入 fake crew 离线验证）；worker/compose 传递 FLOW_MODE+Serper 配置；37 测试 + Ruff + mypy strict 全绿。

**P05-13~P05-15 路线**：P05-13 真实 E2E smoke（默认 skip，需 live 授权 + `.env` 真实 key + `RUN_LIVE_E2E=1`）；
P05-14 20公司×5场景 evals 数据集（可离线）；P05-15 benchmark runner（fake/fixture 验证）。

## 6. 已知风险与注意事项

1. **write_to_file 中文路径截断 bug**：含中文路径下可能截断；写入异常时用 `git status`/`pathlib.Path(...).exists()` 验证。
2. **Ruff W292/E501**：新文件末尾需换行、行超 100 列；提交前 `uv run ruff check --fix <files>`。
3. **P05-12 ✅ 完成**：真实录制 AAPL（as_of=2025-10-31）已提交；普通测试/CI 仍用离线 fixture 回放，不联网。
4. **P05-12A live wiring**：只做 fake/mock 离线 wiring 测试；live 需要真实 API Key 配置，缺失时 fail-fast。
5. **P05-12B 是下一任务**：真实 LLM builder + Agent 统一协议 + 工具注入 + LiveRunner.run，规模跨 6+ 文件，超单任务学习粒度，需按拆分建议逐步实现并与测试对齐。
6. **P05-13 真实 E2E**：需 live 授权 + 本机/Ubuntu `.env` 真实 key（不写入代码/日志/fixture/manifest/Git/镜像）；本窗口未执行（root .env 不存在）。最多 2 次付费尝试。
7. **不 push / 不部署 / 不触发 Docker 构建 / 不进入 P06**。

## 7. Git 状态（本快照）

P05-12 真实补完后工作区已清空（所有变更已 commit）。最新 commit：`da2f07d`。
分支 `agent/m2-deterministic-tools` 领先 origin 27 commits（未 push）。

## 8. 新窗口继续短提示词

> 【Phase 5 继续（安全交接）】请读取 `docs/11-PHASE5-HANDOFF.md` 和
> `docs/05-DEVELOPMENT-ROADMAP.md`，确认当前在 P05-12B。
> 当前分支 `agent/m2-deterministic-tools`，已完成 P05-01 ~ P05-12（全部 ✅，含真实 SEC 录制）
> + P05-12A ✅（最新 commit `da2f07d`），工作区干净。按 P05 自动连续执行规则从 P05-12B 开始继续；
> P05-12B = 真实 LLM builder + Agent 统一 LLM 协议 + 真实工具注入 + LiveResearchFlowRunner.run
> + compose FLOW_MODE 传递 + 离线 contract tests（普通测试/CI 仍用 fake）；
> 缺真实配置 fail-fast，禁止自动退回 fake；P05-13 需 live 授权 + `.env` 真实 key 才执行；
> P05-14/15 可离线完成。
> 每任务完成 → 测试 → ruff/mypy → 标✅ → 学习日志 → commit；
> 不 push / 不部署 / 不进入 P06。上下文接近上限时更新本文件并停止。
