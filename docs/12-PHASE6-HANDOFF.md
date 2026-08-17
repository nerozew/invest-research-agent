# Phase 6 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限或阶段性收口时，把已完成工作、下一任务、测试结果、
> Git 状态完整记录下来，供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`（已 push，CI 全绿）
- **Phase 6 状态**：P06-01~05 ✅；P06-05A ✅；P06-06 ✅；**P06-06A ✅；P06-06B ✅；P06-06C ✅**；P06-07 ✅；P06-08 ✅；P06-09 ✅；**P06-09A ✅；P06-09B ✅；P06-09C ✅**；P06-10~14 未开始。
- **P06-09C**：Prometheus + Grafana + Jaeger 可观测性增强已完成（18 新指标 + 7 Row dashboard + 隔离 fake 栈 smoke PASS + worker 多进程指标修复），已 push 并 4 job CI 全绿。
- **P06-09C CI**：https://github.com/nerozew/invest-research-agent/actions/runs/32046783336
- **P06-10（100 次基准）尚未开始**；下一候选任务，未开始。
- **P06-06**：完整本地 Docker Compose observability profile（Prometheus / Grafana / OTel Collector / Jaeger）已真实启动并通过健康检查。
- **P06-06A**：每任务 fast/deep 研究档位（前端单选 + 0007 迁移 + Worker 路由 + fast 关闭思考模式）。
- **P06-06B**：ProgressSink 实时步骤状态（幂等创建 00-07、合法状态转换、失败收口 running、终态清空 current_step、前端阶段中文映射）。
- **当前验证**：相关测试 87 passed（含新增 SqlProgressSink/前端/ExecutionRecorder 不重复插入）；Ruff 全绿；`mypy src` 110 个源文件全绿；0007 迁移真实 PostgreSQL upgrade→downgrade→upgrade 通过；fast/deep fake smoke 通过（running 期间 current_step+steps、终态 8 步全成功、无真实 API 调用）。
- **当前 commit**：P06-06A 代码 `b2d773e`（已存在）+ 本轮新增（未提交）。

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| P06-01 ✅ | Jinja2 固化 Markdown 报告模板 + golden tests（reporting/renderer.py + report.md.j2） | test_report_renderer 15 passed；全量 708 | `d01a92c`（代码）、`533437e`（docs） |
| P06-02 ✅ | CommonMark→HTML→PyMuPDF Story（4 页样例已逐页视觉检查） | 相关 28 passed | `5c9e2df` |
| P06-03 ✅ | 工件生命周期清理（临时文件/过期任务/.keep 保护/基准保留 N 个；dry_run 默认） | test_artifact_lifecycle 13 passed | `dd2aec7`（代码）、`cf80d9b`（docs） |
| P06-04 ✅ | 生产配置档位：environment=production 占位符密钥 fail-fast + 密钥卫生测试 | test_secrets_config 11 passed | `bc12798`（代码）、`6403b05`（docs） |
| P06-05 ✅ | trace carrier 同 Outbox 持久化，Celery headers 投递，Worker 提取 parent context | trace/outbox/dispatcher 18 passed | `33f0643` |
| P06-05A ✅ | SEC Company Facts 并行预取、filed/as_of 截断、concept mapping 选数、Analysis 硬约束注入 | 相关 42 passed | `4a0b1a9` |
| P06-06 ✅ | 完整本地 Docker Compose observability profile（Prometheus/Grafana/OTel Collector/Jaeger） | test_compose_observability 12 passed；相关 36 passed | 本轮待提交 |
| P06-06A ✅ | 每任务 fast/deep 档位：ResearchProfileMode + 0007 迁移 + 前端单选/徽章 + Worker 路由 | 相关 87 passed（含新增前端/进度）；Ruff/mypy 全绿；0007 真实 PostgreSQL upgrade→downgrade→upgrade 通过 | 代码已在 `b2d773e`（后端）+ 本轮前端 |
| P06-06B ✅ | 实时步骤状态：ProgressSink + SqlProgressSink 短事务/幂等创建 + Flow/Worker 步骤标记 + 前端阶段中文映射 | SqlProgressSink 11、ExecutionRecorder 不重复、前端 19、Worker 87 全绿 | 本轮待提交 |
| P06-06C ✅ | 接通 Prometheus 业务指标与 Jaeger 真实 trace 数据链：OTLP 端点规范化 + 业务指标事件 + Worker 多进程指标端点 + Prometheus/Grafana 修复 | 离线 48+48 passed；Ruff/mypy 全绿；Docker fake 验收：两 target up、metrics/Histogram 非空、Jaeger trace 跨 Outbox/Celery 关联、无 OTLP 404、无真实外部调用 | 本轮待提交 |

## 3. 关键产物文件（P06 增量）

```
compose.yml                                    # P06-06：observability profile（prometheus/grafana/otel-collector/jaeger）
deploy/prometheus/prometheus.yml               # P06-06：采集 API /metrics
deploy/grafana/provisioning/datasources/prometheus.yml  # P06-06：自动 provision Prometheus datasource（uid=prometheus）
deploy/grafana/provisioning/dashboards/dashboard.yml    # P06-06：自动加载 research.json dashboard
deploy/otel-collector.yaml                     # P06-05/06：OTLP HTTP → debug + Jaeger，health_check extension
tests/test_compose_observability.py            # P06-06：12 个静态校验测试
src/invest_research/reporting/renderer.py      # P06-01 Jinja2 渲染器 + build_render_input
src/invest_research/reporting/templates/report.md.j2  # 报告模板（封面/正文/来源链接/限制/免责声明）
src/invest_research/infrastructure/observability/tracing.py  # P06-05 span()/OTLP/可重复配置
tests/fixtures/golden_report.md                # P06-01 golden 基准（逐字节比对）
docs/p06-02-samples/                           # 示例 PDF(4页,42链接)/MD/PNG（真实 AAPL live 工件渲染）
scripts/render_report_pdf.py                   # 示例重新生成脚本
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P06-06 ✅ / P06-07 为下一候选。
- `docs/09-LEARNING-LOG.md`：P06-06 学习条目（3 知识点 + 检查问题 + 已知限制）。
- `docs/10-RUN-GUIDE.md`：此前已含 §7 生产配置与密钥管理。

## 5. 等待人工确认的事项（已清空）

> P06-06 的 observability 服务已全部健康；P06-07 是下一任务（本地部署 smoke test），
> 需用户授权切换到该任务后再执行。

## 6. 已知风险与注意事项

1. **观测镜像体积大、慢网络下首次 pull 超时**：Prometheus v2.55.1 / Grafana 11.3.0 /
   Jaeger 1.62.0 均较大；网络不稳定时 `docker pull` 会 10 分钟超时中断。
   解法：串行重试 `docker pull`（Docker 会断点续传已下载 layer，复用不重复下载）。
2. **OTel Collector 官方镜像是 distroless（无 shell）**：compose healthcheck 用
   `["CMD", "/otelcol", "--version"]` 探测进程存活；真实健康由配置中的
   `health_check` extension（13133 端口）提供。
3. **Jaeger 使用 all-in-one 内存存储**：重启丢失链路数据，仅用于本地查看，不用于生产。
4. **`deploy/` 目录已在 .gitignore 白名单放行**（`!/deploy/`）；新增根目录文件需同步放行。
5. **未 push、未触发 Actions、未部署、未 SSH**：全部按夜间安全规则执行。
6. **Windows WMI 环境故障（历史）**：本机 `platform.system()/machine()` 曾在 WMI 卡死；
   本轮测试与 Docker Desktop 均正常，未再复现。

## 7. Git 状态（本快照）

本轮代码 commit（均为本地，**未 push**）：

- `21b3133` feat(p06-06a): add per-job research profile to frontend（前端档位单选 + 徽章）
- `f7034e6` feat(p06-06b): real-time step progress with SqlProgressSink and minimal frontend stage display（ProgressSink + 步骤标记 + 前端阶段映射 + 测试 + 文档）
- P06-06B 收口新增本地 commit（未 push）：`fix(p06-06b): enforce real-time progress invariants and cancellation cleanup`

### P06-06B 收口要点（2026-08-16）

- **状态不变量**：唯一 running（SQL NOT EXISTS）、current_step 只指向 running、终态清空、attempt_count 原子 +1、00 在 01 前 succeeded；
- **取消收口**：DELETE 经 CancelStepCleanup 端口（SqlProgressSink.cancel_pending_steps）把 running/pending→skipped、清空 current_step、不删历史；
- **孤儿任务已收口（未删行/工件）**：e38d85f0/73ec29cf → cancelled、current_step=null、无 running、pending 已 skipped；
- **fake smoke（fast+deep）**：唯一 running、current_step 与 running 一致、8 步顺序正确、attempt≥1、终态 null、fast/deep 正确、日志无 SEC/Serper/真实模型调用；
- **模型配置（仅复核）**：容器已读取 .env：Research/Analysis=qwen3.6-flash、Writer=qwen3.5-plus、thinking=false；建议 Research 快速模型（thinking=false）、Analysis 更强非思考模型（额度不足用 Flash）、Writer 写作质量模型（thinking=false）；Analysis 是否开 thinking 需后续固定评测决定；
- **前端增强**：中国时区（Asia/Shanghai）简化格式 + 列表/详情创建/开始/结束时间与总耗时。

最终验证：相关测试 87 passed；Ruff `All checks passed`；`mypy src` 110 个源文件全绿；
0007 迁移真实 PostgreSQL upgrade→downgrade→upgrade 通过；fast/deep fake smoke 通过
（running 期间 current_step+steps、终态 8 步全成功、无真实 API 调用）。
本轮未 push、未调真实付费 API（fake smoke 已覆盖）、未 SSH、未部署。

## 8. 新窗口继续短提示词

> 【Phase 6 继续】读取 `docs/12-PHASE6-HANDOFF.md`、`docs/13-ERRORS-REVIEW.md`
> 和 `docs/05-DEVELOPMENT-ROADMAP.md`。P06-01~06 已完成，相关测试 36 passed、
> Ruff/mypy 全绿；完整 Docker Compose observability profile 已真实启动（10 服务健康）。
> 下一步是 P06-07（本地部署 smoke test：health、任务执行、报告下载、指标和链路查询），
> 需用户授权切到该任务；未经授权不 push、不 live API、不 SSH。

---

## P06-09A：FinancialAnalysisPack 结果完整性状态（✅ 已完成）

- `domain/models.py`：新增 `AnalysisCompleteness`（complete/partial/unavailable）StrEnum +
  `schema_version=analysis_pack_v2` + `unavailable_reason` + 跨字段 `model_validator`；
  旧版 `version="analysis_pack_v1"` 工件经 `model_validator(mode="before")` 自动标记 v1
  走宽松兼容分支（不强行套新状态）。
- 提示词：`analysis_prompt_v2.md` / `writer_prompt_v2.md`（loader `PromptName.ANALYSIS/WRITER`
  切到 v2）；Analysis/Writer task description 同步 completeness 语义。
- 质量门禁：`quality_classifier` 增加 `unavailable_with_content` CRITICAL 兜底
  （防御绕过 Pydantic 直接构造 state 的路径）。
- 测试：`tests/test_analysis_completeness.py` 18 用例（三态合法/矛盾被拒/v1 兼容/
  partial、unavailable 不崩溃/门禁兜底）；受影响模块回归 85 passed，ruff/mypy 通过。
- commit：`0bcb303`。

## P06-09B：统一 PackBoundary（✅ 已完成）

- `agents/pack_parsing.py`：新增 `PackSourceKind`（final_answer/tool_params/action_input/
  plain_text）、`BoundaryError`（error_code/stage/field/expected/actual/脱敏 detail）、
  `PackBoundary`（提取 → 来源分类 → JSON/结构 → 多余字段 → schema → 语义跨字段分层校验）。
- 规则：Action Input / 工具调用参数被拒为 NOT_A_PACK；仅 schema 阶段结构错误允许至多一次
  修复（`max_repairs=1`）；semantics（业务跨字段矛盾，如 completeness 与内容冲突）不进入
  格式修复；修复失败返回原始稳定错误分类；`_sanitize` 截断 + 去绝对路径 + 打码密钥字段。
- 接入：`flow_wiring._to_packed` 统一换用 `PackBoundary`（确定性 parse，max_repairs=0），
  单一读取顺序（pydantic → json_dict/exported → raw）与 ArtifactReader/dump_task_output 一致。
- 测试：`tests/test_pack_boundary.py` 18 用例；受影响模块回归 71 passed，ruff/mypy 通过。
- commit：`06c91d1`。

## P06-09C：Prometheus + Grafana + Jaeger 可观测性增强（✅ 已完成）

- `infrastructure/observability/metrics.py` + `metrics_events.py`：新增 18 个指标
  （HTTP RED / Job+profile / stale_recovery / failure / agent / pack / tool-cache / llm）。
- `api/app.py` HTTP RED 中间件；`worker.py` Job 终态/耗时/Gauge/failure 分类 +
  **P06-09C-fix**（PROMETHEUS_MULTIPROC_DIR 提前到模块导入最前，修复 Celery 子进程不写
  .db 导致 9101 聚不到业务指标）；`flow_wiring.py` agent 子 span + pack span；
  `execution.py` job_flow_succeeded/failed 结构化日志（trace_id/span_id 关联 Jaeger）；
  `logging.py` 合并 P05-05 脱敏 + P06-09C structured_extra。
- Grafana dashboard 重构为 7 Row（系统健康/HTTP RED/任务与步骤/Agent/PackBoundary/
  工具与缓存/LLM），全部 histogram_quantile、无高基数 label。
- 验证：`tests/test_grafana_dashboard.py` + `test_metrics_events.py` +
  `test_prometheus_labels.py` + `test_execution_service.py` 41 passed；ruff/mypy 全绿；
  隔离 fake 栈 smoke PASS（20 job 终态、Prometheus/Jaeger/Grafana 全绿、
  worker 无 sec.gov/serper.dev/chat/completions）。详见 `docs/18-P06-09C-VALIDATION.md`。
- 已 push：`4c7b948`、`76d0ab6`、`4a20e06`、`70a65b4`、`7874f7b`、`7785663`、`bc7fd6c`。

### P06-09C Git 检查点与 CI 验收（2026-08-18 ✅）

- **CI run**：https://github.com/nerozew/invest-research-agent/actions/runs/32046783336
  （commit `6c055fd`，P06-09C 最后一个修复 commit）
- **四个 job 结果**：
  - ruff (lint + format)：✅ success
  - mypy (type check src)：✅ success
  - pytest (offline unit)：✅ success（首次运行失败 → 修复后通过）
  - migration + db integration (postgres)：✅ success
- **修复 commit**：`6c055fd`（fix(p06-09c): resolve http.route from matched route
  after middleware call_next）— 修复 Starlette HTTP middleware 在路由匹配前读取
  scope["route"] 得 "<unknown>" 的问题，改为 call_next 返回后补读匹配路由。
- **P06-10（100 次基准）尚未开始**；阶段级 Checkpoint 仍为 deferred。

### 审计结论（P06-09C 验收，未开始 P06-10）

#### 审计 1：Prometheus multiprocess *.db 清理（结论：已覆盖，机制完整）

- Worker 子进程 .db 清理已有三层防护：
  1. `worker_metrics_server.py::cleanup_multiproc_dir()`：`worker_init` 启动
     metrics server 前清空 `PROMETHEUS_MULTIPROC_DIR`（默认 /tmp/prometheus_metrics）
     下所有 `.db`（孤儿 pid 不会残留污染）；
  2. `mark_worker_process_dead(pid)`：`worker_process_shutdown` 信号对每个退出的
     子进程调用 prometheus_client `mark_process_dead`，删除对应 pid_*.db；
  3. 容器重建时 /tmp 自动清空（worker 容器内目录非持久卷）。
- 结论：Worker 重启与下一次基准之间不会复用旧指标；每次新基准前 worker_init
  已清空目录，Gauge/Counter 从 0 起算。无需额外改动。
- 注意：若多次基准在**同一个**长期 worker 进程内先后执行（不重启 worker），
  Counter 会跨基准累加——但这是 Counter 语义，新基准对比应使用
  `increase()` / `delta` 或按时间切分，不构成 .db 污染。

#### 审计 2：observability smoke 统计口径（结论：口径未分开，需 P06-10 前明确）

- `scripts/run_observability_smoke.py` 中 `_NUM_JOBS = 20` 只覆盖"20 个主任务"
  （fast 10 + deep 10），但：
  - 幂等复用场景：首次创建已计入 `created`（复用返回同一 job_id 不再加入），
    不影响总数；
  - **取消测试任务**：`cancel_payload` 创建后 `created.append(..., index=-2)` 会
    把第 21 个 job 加入 `created`，因此 `terminal_counts` 与 `created_count`
    实际统计 21 个 job（20 主 + 1 取消）；
  - 输出中 `fast_count/deep_count` 用 `profile` 统计正是 10/10，但
    `created_count` / `terminal_distribution` 含取消任务未单独标注。
- 结论：**"20 个主任务 + 1 个取消测试任务"的统计口径未明确分开**——报告字段
  `created_count=21` 会让人误以为创建了 21 个主任务。P06-10 开始前应明确：
  （a）`created_count` 改名 `jobs_created_total`（含取消）并额外输出
  `main_jobs_created=20`、`cancel_test_jobs=1`；
  （b）或在结果中单独加 `cancel_test_count` 字段并把 `created_count` 限制为 20。
  本次仅审计不修改（遵守"不开始 P06-10"限制）。

## 阶段级 Checkpoint：deferred（后续可选升级）

- 本轮**不实施**阶段级 Checkpoint；不增加专门的校验 Agent；不把现有工作流改为多个
  独立 Flow。
- 已知限制（不宣称已解决）：当前失败后仍可能重新执行整个 Crew（research/analysis/writer
  三 Agent 从头跑），无阶段级中间断点续跑。稳定性优化可后续评估 Checkpoint 是否纳入
  P06-10 基准之后的升级计划。
- **P06-09C 已 push（CI 4 job 全绿）；P06-10（100 次基准）尚未开始，为下一候选任务。**
- **阶段级 Checkpoint 仍 deferred**（后续可选升级）。

## P06-10A: 100 x benchmark framework + 10 calibration (DONE, P06-10 not started)
- scripts/run_phase6_benchmark.py: e2e HTTP benchmark
- deploy/compose.benchmark.yml: isolated project
- calibrate run_id=7b17ca17a109: 10/10 = 100%, P50=0.334s P95=0.393s
- P06-10 official 100 runs NOT executed (await approval)
