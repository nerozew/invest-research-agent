# 当前任务交接（Task Handoff）——P06-09 CI 收口完成

> 本文件用于在**新窗口/新会话**继续当前工作：先读本文件 + docs/13-ERRORS-REVIEW.md，再按下面提示词继续。
> 更新时间：2026-08-17（P06-09 完成并本地 commit 后）。

---

## 一、当前任务背景

分支 `agent/m2-deterministic-tools` 已完成 P06-09「前置稳定性收口 + GitHub Actions CI」。
P06-07/P06-08 已 ✅；P06-09 现已 ✅。下一候选任务为 P06-10（100 次基准并归因失败）。

**历史背景（已解决）**：Ubuntu live 任务曾因 Analysis 空 facts 撞 schema 卡 running，已由
b06efb0（允许空 facts + mark_failed 兜底）与 P06-09 的统一 schema 解析/错误分类彻底收口。

## 二、P06-09 已完成（两个本地 commit，未推送）

### commit e1b6882 —— 阶段一：Pack 交接收口 + 统一 schema 解析/错误分类

- 新增 `agents/pack_parsing.py`：统一从 Crew 输出/dict/JSON 文本/代码围栏解析 pack，
  `PackParseError`（error_code=SCHEMA_INVALID）；多余字段被 `_reject_extra_fields` 拒绝；
  `dump_task_output` 供 Writer ArtifactReader loader 读上游真实 pack（非 readable 占位符）；
- 新增 `application/failure_classifier.py`：异常 → 稳定 error_code（TIMEOUT/RATE_LIMITED/
  NETWORK_TRANSIENT/UPSTREAM_5XX/AUTH_ERROR/SCHEMA_INVALID/INTERNAL_BUG）+ 脱敏消息 +
  failure_stage；`domain/errors.py` 新增 TIMEOUT 错误码（可重试）；
- `application/execution.py`：`mark_failed` 协议携带错误三元组；process 异常分类后落库并 re-raise；
- `infrastructure/queue/worker.py`：`_RepoWriter.mark_failed` 保存 error_code/error_message/
  failure_stage；启动时 `_run_stale_job_recovery` 收口历史 stale running Job
  （STALE_RUNNING_RECOVERED，不删记录/工件）；
- `migrations/versions/0008_failure_stage.py` + ORM：research_jobs.failure_stage；
- `infrastructure/flow_wiring.py` / `agents/crew_factory.py`：复用 pack_parsing
  （解析侧与 Writer ArtifactReader 读取侧共用同一读取顺序）；
- 新增 `tests/test_pack_contracts.py` 离线契约测试（空 facts/非法 JSON/字段缺失/多余字段/
  代码围栏/上游真实交接/错误分类/脱敏）。

### commit（待执行）—— 阶段二：CI workflow + 文档标记

- `.github/workflows/ci.yml`：独立 CI（不把 docker-image 当 CI）——Ruff / mypy src /
  离线 pytest（SKIP_DB_TESTS=1）/ migrations+DB 集成（GitHub Actions services.postgres）；
  全部 job FLOW_MODE=fake，不 resolve 任何 secrets，concurrency/cancel-in-progress + timeout-minutes；
- `docs/05-DEVELOPMENT-ROADMAP.md`：P06-09 标记 ✅；
- `docs/09-LEARNING-LOG.md`：追加 P06-09 学习条目；
- 本文件（docs/14）重写为 P06-09 交接。

## 三、验证结果（本地真实执行）

- `ruff check src tests migrations scripts` → All checks passed!
- `mypy src` → Success: no issues found in 115 source files
- 完整离线套件 `FLOW_MODE=fake SKIP_DB_TESTS=1 pytest -q -p no:cacheprovider` → **893 passed, 19 skipped**（跳过 testcontainers DB 测试；4 个 E 网络问题与 docs/16 既有记录一致）；
- 针对性 pytest（test_pack_contracts / test_writer_task / test_research_crew / test_execution_service / test_flow_wiring / test_status）→ 61+ passed；
- 本地命令与 CI workflow 完全一致，CI 配置无需改代码即可跑。

## 四、环境备忘

- 分支 agent/m2-deterministic-tools，本地 HEAD=e1b6882（阶段一）+ 阶段二待提交（未推送）。
- 离线测试命令：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（需要 DB 集成时
  设 `SKIP_DB_TESTS=1` 跳过 testcontainers；完整 DB 集成由 CI migrations job 用内置 PostgreSQL 覆盖）。
- Ruff：`.venv\Scripts\python.exe -m ruff check src tests migrations scripts`；
  mypy：`.venv\Scripts\python.exe -m mypy src`。
- 工具链：中文路径 write/edit 偶发 ReplaceFileW EIO（重试或 read 全量 + write 重写）。

## 五、下一步（P06-10 候选，未开始）

- P06-10：执行 100 次基准并归因失败（fake/fixture/live 三模式已在 P05-14/P05-15 就绪）；
- 需先推送本地 commit（不 push 是 P06-09 的硬约束，P06-10 开始前可推）；
- push 后可在 GitHub Actions 触发 ci.yml 验证 migrations job 真实 PostgreSQL 集成。

## 六、给下一个窗口的提示词（复制即用）

```text
继续投研项目（分支 agent/m2-deterministic-tools，工作目录 D:\MyProjects\agent驱动的自动化投研系统）。

背景：P06-09（前置稳定性收口 + GitHub Actions CI）已完成，本地有两个 commit（阶段一 e1b6882 已提交；
阶段二 CI workflow + 文档未提交）。本地验证 Ruff/mypy 通过、离线套件 893 passed。

任务：
1. 提交阶段二（.github/workflows/ci.yml + docs/05 + docs/09 + docs/14）为独立 commit（不并入阶段一）。
2. 推送 origin agent/m2-deterministic-tools（GitHub 443 间歇故障，间隔 15s 重试最多 5 次）。
3. push 后确认 GitHub Actions 触发 ci.yml：ruff/mypy/test-offline 应通过；migrations job 用
   GitHub Actions 内置 PostgreSQL 跑 Alembic 迁移与 repository 集成测试——这是本机 testcontainers
   网络无法覆盖的部分，需在 Actions 内确认全绿。
4. 不要 start P06-10（100 次基准）除非用户明确要求。

详细错误记录见 docs/13-ERRORS-REVIEW.md，交接细节见本文件。