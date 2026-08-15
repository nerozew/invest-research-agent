# Phase 5 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限时，把已完成工作、下一任务、测试结果、Git 状态完整记录下来，
> 供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`
- **已提交 Phase 5 任务**：P05-01 ~ P05-11 全部 ✅；**P05-12 ✅（真实录制）**；**P05-12A ✅**；**P05-12B ✅（真实生产组装）**；**P05-14 ✅（20x5 evals 数据集）**；**P05-15 ✅（benchmark runner）**；**P05-13 实现完成（离线契约通过，无真实 .env，未标 ✅，等待受控 live run）**
- **下一个待执行任务**：**P05-13（受控 live run 授权后执行）→ 之后进入 P06**
- **已补充到路线图的子任务**：P05-03A、P05-12A、P05-12B（见 docs/05-DEVELOPMENT-ROADMAP.md）
- **上下文快照**：P05-12B/13/14/15 完成后已刷新（详见第 8 节）

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
| P05-12B ✅ | 真实 LLM、Agent 工具和 LiveResearchFlowRunner 生产组装（离线验收通过） | 37 测试全绿 + ruff/mypy | `78cb90f` |
| P05-13 ⏳ | 真实 E2E smoke（opt-in，实现完成） | 离线契约 3 passed + live skip；无真实 .env | `1f93fc9`（含） |
| P05-14 ✅ | 20 公司 × 5 场景 evals 数据集 | 10 测试全绿 | `1f93fc9` |
| P05-15 ✅ | benchmark runner + 汇总（fake/fixture/live） | 4 测试 + fake 100 条 success=1.0 | `eb3b743` |

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

- `docs/05-DEVELOPMENT-ROADMAP.md`：P05-01 ~ P05-11 全部 ✅；**P05-12 ✅（真实录制）**；**P05-12A ✅**；**P05-12B ✅**；**P05-13 实现完成（等待 live run，未标 ✅）**；**P05-14 ✅**；**P05-15 ✅**
- `docs/09-LEARNING-LOG.md`：P05-01 ~ P05-12 + P05-12A + P05-12B + P05-13/14/15 学习条目已添加（含检查问题，未附答案）

## 5. P05-12B 之后的路线

**P05-12B 已完成（离线验收）**：真实 LLM builder（CrewAI 1.6.1 实测 `LLM(model, base_url, api_key, temperature, timeout)`）；统一 `AnyLLM` 接口；真实工具注入（real_tools.py，Research=6 工具白名单）；`LiveResearchFlowRunner.run` 完整控制流（Crew→pack→质量门禁→受控反思→manifest→工件落盘，可注入 fake crew 离线验证）；worker/compose 传递 FLOW_MODE+Serper 配置；37 测试 + Ruff + mypy strict 全绿。

**P05-13~P05-15 状态**：P05-14 ✅（20 公司×5 场景 evals 数据集）；P05-15 ✅（benchmark runner，fake/fixture/live）；
**P05-13 仍待 live（未标 ✅）**：真实 E2E smoke（默认 skip，需 live 授权 + `.env` 真实 key + `RUN_LIVE_E2E=1`）。

## 6. 已知风险与注意事项

1. **write_to_file 中文路径截断 bug**：含中文路径下可能截断；写入异常时用 `git status`/`pathlib.Path(...).exists()` 验证。
2. **Ruff W292/E501**：新文件末尾需换行、行超 100 列；提交前 `uv run ruff check --fix <files>`。
3. **P05-12 ✅ 完成**：真实录制 AAPL（as_of=2025-10-31）已提交；普通测试/CI 仍用离线 fixture 回放，不联网。
4. **P05-12A live wiring**：只做 fake/mock 离线 wiring 测试；live 需要真实 API Key 配置，缺失时 fail-fast。
5. **P05-13 真实 E2E（待 live）**：需 live 授权 + 本机/Ubuntu `.env` 真实 key（不写入代码/日志/fixture/manifest/Git/镜像）；未执行（root .env 不存在）。最多 2 次付费尝试。
6. **Phase 5 收口审计已修复**：test_llm_factory.py 过期 NotImplementedError 断言已替换为真实 builder 验证（不联网/参数映射/密钥不泄露）；`mypy src` 已全绿；Ruff 全绿；pytest 634 passed。
7. **部署状态**：docker-image.yml/compose.yml 已切到 `phase5`（artifact 名为 `invest-research-phase5-image`）；新增 `scripts/deploy_phase5.sh`；**尚未触发 GitHub Actions phase5 构建、未部署、未进入 P06**。

## 7. Git 状态（本快照）

最新 commit：`6fc28d8`（docs(p05): finalize phase5 handoff - P05-12B/14/15 done, P05-13 impl done awaiting live run）。
分支 `agent/m2-deterministic-tools` 已 push 至 GitHub（远端 HEAD 为 `6fc28d8`）。
GitHub Actions #15 已成功，但构建的是 **phase4 标签**（phase5 镜像尚未构建）。
P05-13 尚未执行真实 live E2E。

## 8. 新窗口继续短提示词

> 【Phase 5 继续（安全交接）】请读取 `docs/11-PHASE5-HANDOFF.md` 和
> `docs/05-DEVELOPMENT-ROADMAP.md`。
> 当前分支 `agent/m2-deterministic-tools`（已 push，远端 HEAD=`6fc28d8`），
> 已完成 P05-01 ~ P05-12 + P05-12A/B ✅，P05-14/15 ✅，
> P05-13 实现完成（离线契约通过，无真实 .env，**未标 ✅**，等待受控 live run）。
> 最近一次收口审计：pytest 634 passed / 19 skipped；ruff check 全绿；`mypy src` 全绿。
> 部署文件已切 phase5（docker-image.yml / compose.yml / `scripts/deploy_phase5.sh`），
> 但**尚未触发 GitHub Actions phase5 构建、未部署**（Actions #15 构建的是 phase4 标签）。
> P05-13 真实 E2E 需：项目根目录创建 `.env`（LLM_API_KEY + SERPER_API_KEY + FLOW_MODE=live），
> 然后 `export RUN_LIVE_E2E=1 FLOW_MODE=live && uv run pytest tests/test_live_e2e.py -v`
> （最多 2 次付费尝试）；P05-13 通过后才可进入 P06。
> 每任务完成 → 测试 → ruff/mypy → 标✅ → 学习日志 → commit；
> 不部署 / 不进入 P06。上下文接近上限时更新本文件并停止。
