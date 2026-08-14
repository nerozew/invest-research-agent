# Phase 5 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限时，把已完成工作、下一任务、测试结果、Git 状态完整记录下来，
> 供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`
- **已提交 Phase 5 任务**：P05-01 ✅、P05-02 ✅、P05-03 ✅、P05-03A ✅
- **下一个待执行任务**：**P05-04（实现输入 hash 和下游失效 / versioning service）**
- **已补充到路线图的子任务**：P05-03A、P05-12A（见 docs/05-DEVELOPMENT-ROADMAP.md）

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| P05-01 ✅ | 通用 retry policy（Tenacity：白名单错误 + 指数退避 + 抖动 + 可注入 sleep） | 14 passed | `ca25c08` |
| P05-02 ✅ | 尊重 `Retry-After` 并动态降速（秒/HTTP-date 双格式 + Provider 注入） | 26 passed | `a5591da` |
| P05-03 ✅ | 步骤 lease 与 stale recovery（StaleStepSnapshot / StepLeaseStore 端口 / StaleRecoveryService） | 6 passed | `98e56ca` |
| P05-03A ✅ | Worker 启动自动 stale recovery + RecoveryCounter 计数 | 9 passed | `04322f5` |

上次/最新一次完整静态检查：全部任务后 `ruff check` + `mypy`（strict）均通过。

## 3. 完成任务的产物文件

```
src/invest_research/infrastructure/retry.py           # P05-01/02 核心
src/invest_research/application/recovery.py            # P05-03/03A 核心
src/invest_research/infrastructure/queue/recovery_bootstrap.py  # P05-03A
tests/test_retry_policy.py                            # 14 tests
tests/test_retry_after.py                             # 12 tests
tests/test_recovery.py                                # 6 tests
tests/test_recovery_bootstrap.py                      # 3 tests
pyproject.toml  (新增 tenacity)
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P05-01/02/03/03A 已标 ✅；P05-03A/P05-12A 已补充进表格
- `docs/09-LEARNING-LOG.md`：P05-01、P05-02、P05-03、P05-03A 学习条目已添加（含检查问题，未附答案）

## 5. 下一任务 P05-04 的准备工作

**任务**：实现输入 hash 和下游失效（versioning service）
**路线验收**：prompt/schema 变化触发正确重算
**关键背景**（已读取）：
- `docs/04-WORKFLOW-RELIABILITY.md §6`：步骤幂等键为 `job_id + step_name + input_hash + schema_version`；
  提示词/模型/公式/schema 版本变化后 input hash 改变，相关下游步骤必须失效重算。
- `src/invest_research/prompts/loader.py`：已有 `prompt_sha256(name)`（P03-02 提供）。
- `src/invest_research/flows/manifest.py`：已有 pack checksum（P03-14）。
- `src/invest_research/domain/models.py`：各 pack 带 `version` 字段（如 `research_pack_v1`）。

**建议实现**（P05-04 最小切片）：
1. 新增 `src/invest_research/application/versioning.py`：
   - `compute_input_hash(stage, request, upstream_versions, prompt_hashes) -> str`（sha256）
   - `should_recompute(current_hash, stored_hash, schema_version) -> bool`
   - `VersioningService`：组合 hash 计算 + 失效判断
2. 新增 `tests/test_versioning.py`：
   - 同输入同 hash；改 prompt 内容 → hash 变 → 判定下游需重算
   - 公式版本变化 → 重算；无变化 → 复用
3. 运行 `uv run pytest tests/test_versioning.py -q` + `ruff check` + `mypy`
4. 更新路线图✅ + 学习日志，commit `feat(p05-04): add input hash and downstream invalidation`

## 6. 已知风险与注意事项

1. **write_to_file 中文路径截断 bug**：在含中文的目录（本工作区根目录含中文）下，`write_to_file` 在部分文件（如 `recovery.py`、`recovery_bootstrap.py`、`test_recovery_bootstrap.py`）时偶尔出现路径截断（误创建 `src/invest_res`、`tests/test`）。已用 `del` 清理。
   **应对**：文件写入失败/异常时，先用 `git status` / `py -c "import pathlib; print(pathlib.Path(...).exists())"` 验证；必要时重试 write_to_file 或用 `replace_in_file`（需先 read_file）。
2. **tenacity 新版 API**：`Retrying` 实例是可调用对象（`retrying(fn)`），新版无 `.call()` 方法（P05-01 已遇到过）。
3. **Ruff I001 import 排序 / W292 结尾换行**：新文件提交前先 `uv run ruff check --fix <files>`。
4. **P05-12 需要真实 SEC 数据**：如无有效 SEC 联系邮箱，暂停外部录制，只完成离线 fixture 框架与脱敏器。
5. **P05-13 需要真实千问/搜索 API**：不得擅自产生费用；无授权时保持未完成状态并继续 P05-14/15。
6. **不 push / 不部署 / 不触发 Docker 构建**（Phase 5 全部完成后再确认）。

## 7. Git 状态（本快照）

完成 P05-03A 后工作区已清空（所有变更已 commit）。最新 commit：`04322f5`。

## 8. 新窗口继续短提示词

> 【Phase 5 继续（安全交接）】请读取 `docs/11-PHASE5-HANDOFF.md` 和
> `docs/05-DEVELOPMENT-ROADMAP.md`，确认当前在 P05-04。
> 当前分支 `agent/m2-deterministic-tools`，已完成 P05-01/02/03/03A（均已 commit），
> 无未提交修改。按 P05 自动连续执行规则，从 P05-04 开始继续，
> 每任务完成 → 测试 → ruff/mypy → 标✅ → 更新学习日志 → commit → 自动继续
> → 直到 P05-15 和 Phase 5 最终检查；P05-12 真实 SEC 与 P05-13 真实模型
> 未经授权不得执行 live 调用。上下文接近上限时更新本文件并停止。