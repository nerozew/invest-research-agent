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
- **P06-11K 系列（K-1~K-5）已全部完成 ✅（最新 2026-08-20）**：
  - K-1 `295611e`：settings/.env.example 诊断 5 配置 + diagnostics/{models,redaction,sink,__init__}.py + BoundedDiagnosticBuffer
  - K-2 `69d0f4b`：diagnostics/persistence.py + diagnostics_bundle_store.py（原子写 + 幂等 + 清理）
  - K-3 `96d00b0`：diagnostics/capture.py + flow_wiring run() 收口 + diagnostics_factory 透传
  - K-4 `ea56a55`：工具/SEC/Serper/LLM 脱敏摘要 + Jaeger 白名单属性 + 诊断包安全下载 + 前端失败诊断页
  - K-5（本窗口，待 commit）：worker 生产接线补齐（diagnostics_provider/diagnostics_factory/set_job_id）+ flow_wiring/real_tools/llm_full_observer trace_id 统一注入（current_trace_ids）+ tests/test_p06_11k5_fake_docker_injection.py（7 用例）+ docs/05/09/12 更新
- **K-5 验证**：7 新测试 passed + K-1~K-4/J/工具 span 回归 69 passed；Ruff 全绿；mypy 受检 6 文件无问题（research_flow.py:89 detach 为既有错误，按约束不修）。
- **当前 commit**：P06-06A 代码 `b2d773e`（已存在）+ P06-11K-1~K-4（295611e/69d0f4b/96d00b0/ea56a55，均未 push）+ 本轮 K-5（待提交）。
- **下一候选**：P06-11（10 家公司效率对照实验）或 P06-12（完善 README 演示、架构图和限制）。

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

## P06-11A: LLM Token、延迟与调用链可观测性准确性收口（DONE, not pushed）
- `infrastructure/observability/llm_call_observer.py`: 订阅 CrewAI LLMCall* 事件，每次真实模型调用只计数一次；token 取事件 `usage` 正式字段；duration 用 Started→Completed/Failed 本地实测；Jaeger `llm.request` span 属性仅 provider/model/role/status + duration/tokens。
- `flow_wiring.py`: kickoff 集成 observer；删除 CrewOutput.token_usage 复制给三角色的 三倍计数。
- `deploy/.../research.json`: LLM Row 增加 Token 1h/24h、成功/失败 1h/24h、P50/P95/P99、 usage missing 1h、provider/model/role 筛选变量；PromQL 用 rate/increase。
- `tests/test_llm_observability.py` 20 用例通过 + `test_grafana_dashboard.py` PromQL 可解析； 回归 55（flow_wiring/metrics）+ 46（observability/tracing）全绿；未执行真实付费调用。
- 未 push；未运行 100 次基准；未 docker compose down -v。

## P06-11B: 修复 DeepSeek 与 CrewAI 结构化输出不兼容（DONE, not pushed）

- **根因**：CrewAI 1.6.1 `task.py:_export_output`（L768）中 `output_pydantic` 与 `output_json`
  都进入同一个 `convert_to_model`；Agent 最终文本无法直接通过 Pydantic 校验时进入
  `Converter.to_pydantic`，在 `llm.supports_function_calling()` 时调用
  `llm.call(..., response_model=...)` → OpenAI SDK beta.chat.completions.parse →
  json_schema response_format → DeepSeek 普通 Chat Completion HTTP 400
  （"This response_format type is unavailable now"）。
- **修复（按 LLM_VENDOR 集中决策，不靠 base_url 猜测）**：
  - `agents/llm_factory.py`：新增 `StructuredOutputMode`（NATIVE_PYDANTIC /
    JSON_TEXT_LOCAL_VALIDATION）与 `structured_output_mode(config)`（qwen→原生、
    deepseek/generic→JSON 文本 + 本地校验）。
  - `agents/analysis_task.py` / `agents/writer_task.py`：JSON 文本路径下不绑定
    `output_pydantic`（也不改用 `output_json`——同一 Converter 路径）；提示词追加
    "最终答案只能是一个 JSON object、不要 Markdown 围栏、不要解释文字、
    工具参数不能作为最终答案"；Crew 完成后由现有 `PackBoundary` 本地解析。
  - `domain/errors.py`：新增稳定错误码 `STRUCTURED_OUTPUT_UNSUPPORTED`（非重试）。
  - `application/failure_classifier.py`：识别 response_format/json_schema 拒绝，
    优先于迭代耗尽分类。
  - `infrastructure/flow_wiring.py`：kickoff 异常优先分类为
    `STRUCTURED_OUTPUT_UNSUPPORTED`；同一次执行同时迭代耗尽时保留根因并记录前置信息。
- **验证**：`tests/test_p06_11b_deepseek_structured_output.py` 27 用例通过
  （DeepSeek 三角色不绑 output_pydantic/output_json、Qwen 保留原生路径、
  本地解析为三个 Pack、缺字段仍失败、工具参数不被当 Pack、response_format 400
  分类为 STRUCTURED_OUTPUT_UNSUPPORTED、API Key 不泄露、三角色模型配置生效）；
  回归 143 passed（pack boundary/contracts、research/analysis/writer task、
  llm_factory、crew_factory、flow_wiring、final_report_artifacts）；Ruff 全绿；
  `mypy src` 116 个源文件全绿。
- 未执行真实 DeepSeek/Qwen 付费调用（遵守限制）；未 push；未启动 Docker；
  未运行 100 次基准；未进入下一任务。
- **下一候选任务**：`P06-11`（10 家公司效率对照实验）或 `P06-12`（README 演示）。
- **Docker 重建与一次 fast live 验收步骤**（供后续执行）：
  1. `docker compose build api worker`（重建镜像使 flow_wiring 变更生效）；
  2. `docker compose up -d prometheus grafana jaeger api worker`（jaeger 需已含于 compose）；
  3. 以 `research_profile=fast` 创建一个真实任务（需有效 LLM_API_KEY），等待 succeeded；
  4. 验证 Prometheus `llm_requests_total{status="success"}` 计数 > 0 且按 role 正确、 `llm_tokens_total` 有 input/output、`llm_usage_missing_total` 仅在真实无 usage 时出现；
  5. Jaeger 搜索含 `llm.request` span，属性仅含 llm.provider/model/role/status/duration_s/tokens_total；
  6. Grafana LLM Row 中 Token 1h/24h 面板有非空数据、P50/P95/P99 有曲线、筛选变量可用；
  7. 完成后把结果追加到 docs/18 或本文件（不得自行 push）。

## P06-11D: 修复 DeepSeek Writer 普通文本无法转换 ReportDraft（DONE, not pushed）

- **根因**：P06-11C 之后 writer 第三个输出仍失败——DeepSeek Writer 只通过提示词要求 JSON，
  没有服务端结构化约束（`response_format=json_object` 未启用），实测返回 12 token 左右
  简短自然语言说明 → `_to_packed` → `PackBoundary.identify_source_kind` 分类为
  `PLAIN_TEXT` → `NOT_A_PACK "输出是普通自然语言，不是结构化 Pack"`。
- **修复（原文透传 + 本地确定性组装）**：
  - `application/report_draft_assembler.py`（新增）：`ReportDraftAssembler`（确定性组装器，
    不调用 LLM）——version 代码固定 `report_draft_v1`；title 从
    `ResearchPack.company_identity.legal_name/ticker` + `as_of_date` 确定性生成；
    markdown 原文保留（引号/换行/表格无需 JSON 转义）；citation_keys 只从可信候选集合
    （sources→`src_<hash>`、facts→`fr_<hash>`、传入 claim_keys）∩ 正文实际出现提取，
    模型无法伪造。拒绝规则：空文本/过短(<200)/拒绝短语/工具 Action/缺任何必需章节 →
    REPORT_INVALID；finish_reason=length 或末尾未完标志 → REPORT_TRUNCATED。
  - `agents/writer_task.py`：DeepSeek/generic Writer 提示词改为"直接输出完整 Markdown
    报告正文"，不再要求 JSON 包装/围栏。
  - `infrastructure/flow_wiring.py`：新增 `_extract_report_draft` 统一路径——ReportDraft
    实例/合法 JSON/dict 直接复用（Qwen/历史兼容），否则按 Markdown 交给
    ReportDraftAssembler；ReportAssemblerError → LiveFlowExecutionError（稳定错误码）。
  - `domain/errors.py`：新增 `REPORT_INVALID`/`REPORT_TRUNCATED`（均不可重试）。
  - `tests/test_p06_11d_report_draft_assembler.py`（新增 26 用例，覆盖 20 项契约）。
- **验证**：`tests/test_p06_11d_report_draft_assembler.py` 26 用例通过（合法 Markdown →
  ReportDraft、version 由代码固定、title 从可信身份生成、citation_keys 确定性提取且不可
  伪造、空/过短/Tool Action/Action Input/“无法生成报告”拒绝、finish_reason=length 截断
  拒绝、缺必要章节拒绝或进入 Quality Gate、Markdown 引号/换行/表格无需 JSON 转义、
  DeepSeek 不再要求完整 ReportDraft JSON、不触发 beta、Writer 读取组装后
  FinancialAnalysisPack、Qwen 路径回归、08/09 发布回归、错误消息脱敏、不打印完整报告、
  REPORT_INVALID/REPORT_TRUNCATED 不可重试、一次 fake 全链产生合法 ReportDraft）；
  回归 111 passed（p06-11b / p06-11c / pack_boundary / pack_contracts / flow_wiring /
  research_crew）+ 70 passed 1 skipped（live_e2e / e2e_fake / report_renderer /
  pdf_renderer / final_report_artifacts / flow_quality / quality_classifier /
  quality_models）；Ruff 全绿；`mypy src` 118 个源文件全绿。
- 未执行真实 DeepSeek/Qwen 付费调用（遵守限制）；未 push；未启动 Docker；
  未运行 100 次基准；未进入下一任务。
- **下一候选任务**：受控 live 验证 DeepSeek Writer 输出 Markdown 能被
  ReportDraftAssembler 组装、质量门禁照常工作；随后 `P06-11`（10 家公司效率对照实验）
  或 `P06-12`（README 演示）。

## P06-11C: 修复 DeepSeek 普通 JSON 路径的嵌套 FinancialFact 契约丢失（DONE, not pushed）

- **根因**：P06-11B 让 DeepSeek 走"普通 JSON → PackBoundary 本地校验"，但任务描述仍要求
  模型输出完整 `FinancialAnalysisPack`（含嵌套 FinancialFact）。JSON 文本路径下模型没有
  获得完整嵌套契约（尤其必填 `company_id`/`source_id`），重抄 JSON 时丢字段 →
  `facts[0].company_id: Field required`。审计确认预取 `_serialize_facts` 已含 company_id
  （CIK）与规范化 source_id，因此是 **LLM 重抄丢字段**，非 SEC 数据缺失。
- **公司身份语义**：`FinancialFact.company_id` = SEC CIK（10 位数字，来自
  `SECCompanyFactsTool._to_fact(company_id=cik)`）；未更名、未改数据库结构。
- **修复（LLM 选择，代码组装）**：
  - `domain/models.py`：新增 `AnalysisSelectionDraft`（只含 selected_fact_refs /
    metric_results / notes / limitations / completeness / unavailable_reason）。
  - `application/analysis_assembler.py`（新增）：`build_fact_ref`（sha256 稳定短 hash，
    payload=company/concept/period/unit/accession/form_type/fiscal_period，**不含 source_id**
    ——两侧表示不一致会导致 FACT_REFERENCE_UNRESOLVED）、`AnalysisPackAssembler`（ref→原始
    FinancialFact 唯一匹配；未解析→FACT_REFERENCE_UNRESOLVED、多重→FACT_REFERENCE_AMBIGUOUS、
    公司身份不一致→FACT_PROVENANCE_MISMATCH；重复 ref 幂等去重）、`parse_fact_records`。
  - `infrastructure/real_tools.py`：`_serialize_facts` 每条事实追加 `fact_ref`（LLM 输入契约）。
  - `agents/analysis_task.py`：DeepSeek/generic 提示词要求只输出 AnalysisSelectionDraft，
    禁止抄写 FinancialFact、禁止修改/猜测 SEC 数值。
  - `agents/crew_factory.py`：Writer 的 `_task_output_loader` 注入 `analysis_facts`，
    检测 Draft 时现场组装为 FinancialAnalysisPack（Writer 只读组装后的 pack）。
  - `infrastructure/flow_wiring.py`：`_parse_prefetched_facts` 从 prefetch JSON 还原
    原始事实；`_extract_analysis_pack` 检测 `selected_fact_refs` → Draft 组装，
    失败转 LiveFlowExecutionError（稳定 error_code，failure_stage=04_analysis）。
  - `domain/errors.py`：新增 `FACT_REFERENCE_UNRESOLVED`/`FACT_REFERENCE_AMBIGUOUS`/
    `FACT_PROVENANCE_MISMATCH`（均不可重试）。
  - `prompts/analysis_prompt_v2.md`：输出契约改为"LLM 选择，代码组装"（AnalysisSelectionDraft），
    保留 `analysis_pack_v2` 引用。
- **验证**：`tests/test_p06_11c_analysis_assembler.py` 26 用例通过（真实 SEC fixture 含
  company_id/source_id、model 只返回 fact_ref、assembler 恢复完整 FinancialFact、
  最终 pack 字段与原始输入一致、LLM 无法改写 value/unit/period、未解析/多重匹配/来源不一致
  稳定失败、重复 ref 幂等去重、unavailable+空 refs 合法、partial 必须 limitations、
  complete 必有 facts/metrics、company_id 仍必填、缺 company_id 完整 fact 不静默通过、
  DeepSeek 不绑 output_pydantic、Qwen 原路径不受影响、Writer loader 读组装后 pack、
  错误不泄露 API Key、无常量补 company_id、端到端离线契约）；
  回归 219 passed（p06-11b / pack_boundary / pack_contracts / analysis_task /
  analysis_completeness / flow_wiring / writer_task / research_crew /
  final_report_artifacts / real_tools / models / prompts）；Ruff 全绿；
  `mypy src` 117 个源文件全绿。
- 未执行真实 DeepSeek/Qwen 付费调用（遵守限制）；未 push；未启动 Docker；
  未运行 100 次基准；未进入下一任务。
- **下一候选任务**：受控 live 验证 DeepSeek 输出 Draft 能被本地 Assembler 组装；
  随后 `P06-11`（10 家公司效率对照实验）或 `P06-12`（README 演示）。

## P06-11E：DeepSeek 原生 JSON Finalizer + 三阶段执行拆分（✅ 已完成 2026-08-19）

**任务目标**：不增加 Agent；为 DeepSeek 建立供应商原生结构化输出层——
普通 Agent 工具循环 → 独立 JSON Finalizer → BoundaryCanonicalizer → Pydantic →
确定性 PackAssembler；并把一次 kickoff 拆为 Research/Analysis/Writer 三阶段短路执行。

**新增文件**：
- `application/structured_finalizer.py`：`StructuredFinalizer` 供应商无关端口（Protocol[T]）+
  `FinalizerError`（稳定 error_code）。输入原始 Agent 输出 + 目标草稿类型，输出本地
  Pydantic 校验草稿；不执行工具、不修改原始事实、最多一次格式修复、状态限定当前 Job。
- `infrastructure/finalizers/deepseek_json_object_finalizer.py`：
  `DeepSeekJsonObjectFinalizer`——普通 `chat.completions.create` +
  `response_format={"type":"json_object"}`；`tools=[]`、`thinking=false`（deepseek
  `extra_body`）；检查 `finish_reason`（length 稳定失败）、空 content 稳定失败；
  `json.loads` → BoundaryCanonicalizer → Pydantic；第一次 Schema 失败只修复一次，
  第二次失败立即终止；可注入 mock client（零真实网络）。
- `application/boundary_canonicalizer.py`：仅语义等价规范化（""→None、strip、
  不修改数字/枚举、不补 company_id/source_id、unavailable+空原因仍失败）；
  白名单只覆盖 AnalysisSelectionDraft/FinancialAnalysisPack 的
  unavailable_reason/analysis_notes/limitations。
- `application/research_assembler.py`：`ResearchPackAssembler`——LLM 只选 URL，
  本地从可信 SEC 申报记录构造 Source（canonical_url 必须命中 primary_document_url）；
  无来源 → ResearchAssemblerError（禁止伪造 ResearchPack）。
- `tests/test_p06_11e_deepseek_json_finalizer.py`：22 用例，覆盖 20 项契约。

**修改**：`domain/models.py`（`ResearchSelectionDraft`）、`infrastructure/flow_wiring.py`
（`_RunContext` 每次 run 独立：finalization_count/prefetch_result/analysis_facts 限定当前
Job——修复 `_finalize_used` 跨 Job 泄漏；生产路径三阶段 `_run_staged` 短路，前序成功才
执行下一步；注入 fake crew 保持一次 kickoff 兼容；`_staged_artifact_loader` 让 Writer
只读已组装 pack）。

**验证**：22 新测试全绿；Ruff 全绿；mypy 7 个源文件全绿；回归 `tests/test_flow_wiring.py`
25 passed。测试覆盖：请求体含 json_object、绝不含 json_schema、仅 create 不触发
beta.chat.completions.parse、tools=[]、thinking=false、合法 JSON、普通文本经一次
Finalizer、""→None、unavailable 空原因失败、finish_reason=length/空 content 失败、
一次修复成功、第二次失败终止、Research 失败跳过 Analysis、Analysis 失败跳过 Writer、
Writer 用 ReportDraftAssembler、双 Job 状态隔离、Qwen 回归、fake E2E、Mock client
零真实网络。

**commits（本地，未 push）**：
- `63e1f7f` P06-11E1：StructuredFinalizer 端口 + DeepSeekJsonObjectFinalizer +
  BoundaryCanonicalizer + _RunContext 修复
- `ceabd60` P06-11E2：ResearchSelectionDraft/ResearchPackAssembler + 三阶段执行拆分
- `fdd1b8f` test(P06-11E)：22 项契约测试
- `5fc3267` docs(P06-11E)：路线图标记 ✅
- `62addc2` docs(P06-11E)：学习日志
- `a84578c` docs(P06-11E)：Phase 6 handoff 追加
- `2b50031` fix(P06-11E)：Research 分阶段选草稿无来源时回退有界结构化收尾（真实 live 暴露）

**真实 live 验收（2026-08-19，Docker `agent-*` 栈 + DeepSeek `deepseek-v4-flash`）**：
- 配置：FLOW_MODE=live、LLM_VENDOR=deepseek、模型 deepseek-v4-flash。
- 首跑 job `2b7fd28b`：三阶段+Finalizer 生效（02_research running→failed，
  Analysis/Writer 未执行——短路正确），失败原因「Research 选择草稿没有命中任何
  可信 SEC 申报来源」——`_cache_filings_if_available` 未预热。
- 修复 `_exec_research_stage`：选草稿无缓存来源 → 回退 `_finalize_research_pack`
  （有界结构化收尾，不伪造来源）。
- 重跑 job `c033e758`（fast，MSFT，as_of=2025-10-31，10-K）：
  **succeeded（72.4s）**，02_research→04_analysis→05_writer→06_quality_gate→
  07_manifest 全 succeeded；8 工件齐全（含 08_report.md/09_report.pdf）。
  质量门禁正确拒绝：`["citation_keys 为空", "禁止的投资建议: 买入/卖出/目标价"]`
  → recommendation=rejected（模型报告缺引用且含投资建议，门禁按设计拒绝）；
  manifest.evidence 显示真实外部调用 sec_submissions=1、web_search=1、
  sec_company_facts=1。
- **结论**：DeepSeek 原生 JSON Finalizer + BoundaryCanonicalizer + 三阶段执行 +
  确定性 Assembler 在真实 DeepSeek 下全链路跑通；质量门禁与禁止项按设计工作。

**限制（真实 live 已验收）**：Finalizer 修复未携带结构化字段错误（`_extract_field_errors`
空列表）；首次真实 live 暴露"选草稿无可信缓存来源"→ 已回退有界收尾修复（commit
`2b50031`）；未 push；未运行 100 次基准。

**下一候选任务**：受控 live 下持续观察 DeepSeek 报告的引用与禁止项质量（本次报告被
门禁 rejected——缺 citation_keys 且含买入/卖出/目标价），优化 Writer 提示词后重测；
随后 `P06-11`（10 家公司效率对照实验）或 `P06-12`（README 演示）。
---

## P06-11F：Writer 引用注册表 + 质量门禁准确性 + 有界修订（✅ 已完成 2026-08-19）

**任务目标**：不修改 DeepSeek 结构化输出、不增加第二个模型；修复 Writer 看不到合法 citation key、禁止项误判、质量失败后缺少有界修订三个问题。

**根因审计结论**：
- `citation_keys` 为空根因：`WriterContextReader` 只返回 research_pack/analysis_pack/required_sections，**没有把最终 src_<hash>/fr_<hash> key 交给 Writer**。这些 key 是 Writer 输出 Markdown **之后**才由 ReportDraftAssembler 从 pack 计算，导致 Writer 无法写出合法 key → 提取为空。
- 禁止项误判确认存在：`FORBIDDEN_PATTERNS=("买入","卖出","目标价","建议持仓")` 纯子串匹配，"本报告不构成买入、卖出或目标价建议"必然被误判为 CRITICAL。
- 修订未接入：`_run_reflection` 只做路由记录，从未实际执行定向修订；且 `build_revision_task` 绑 `output_pydantic=ReportDraft`（DeepSeek 400）。

**新增文件**：
- `application/citation_registry.py`：`CitationRegistry/CitationRegistryEntry`——src 复用 `build_source_citation_key`（URL sha256 前 12 位）、fr 复用 `build_fact_ref`；`build_citation_registry()` 是唯一生成来源；`as_writer_payload()` 完整交给 Writer。
- `application/empty_value_policy.py`：`EmptyPolicy`（REQUIRED/CONDITIONALLY_OPTIONAL/OPTIONAL）+ `check_analysis_completeness`——集中式空值策略；complete 缺核心事实 REJECT、partial 缺 limitations REJECT、partial/unavailable 有说明 → PUBLISH_PARTIAL。
- `application/deterministic_revision.py`：纯函数有界修订（不调用 LLM/不走网络）——missing_citation_keys 包装合法 key、invalid_citation_key 删除伪造 key、forbidden_advice 删除建议行；注册表为空不伪造。

**修改**：`report_draft_assembler.py`（`build_source_citation_key` 公共化 + `assemble(registry=...)`）、`writer_task.py`（WriterContextReader 交付完整注册表 + prompt 引用格式 [src_<hash>]/[fr_<hash>]）、`flows/quality_classifier.py`（禁止项上下文正则 + 句子分隔符回溯免责语境 + 非法 key CRITICAL + v1 兼容）、`flows/state.py`（citation_registry + revision_attempted/succeeded）、`infrastructure/flow_wiring.py`（`_apply_bounded_revision` 有界一次修订 + 重新组装 + 重新门禁 + revision 指标）、`metrics.py/metrics_events.py`（`revision_total` Counter）。

**验证**：
- `tests/test_p06_11f_citation_registry_quality_revision.py` 22 用例全绿（WriterContextReader 可见 key / 同一算法 / 合法 key 非空 / 伪造拒绝 / 外部事实空引用 REVISE / 建议买入与目标价拒绝 / 免责声明不误判 / 一次修订通过 / 二次停止 / 纯确定性 / fake E2E 门禁通过）。
- 回归：quality_classifier+flow_wiring+flow_quality+metrics 89 passed；P06-11B~E+writer+revision+models 168 passed；e2e_fake+final_report_artifacts+pack 66 passed。合计 22+89+168+66 = 345 passed。
- `ruff check src` 全绿；`mypy src` 全绿。

**commits（本地，未 push）**：P06-11F 独立 commit（见本轮 git log）。

**限制**：修订器只修复三类可修复 issue；missing_section 不自动补造章节；未修改 P06-11E Finalizer 与 staged flow。

**下一候选任务**：受控 live fast smoke（`LLM_VENDOR=deepseek` 一次真实任务验证注册表交付 + 引用非空 + 禁止项不误判）；随后 `P06-11`（10 家公司效率对照实验）。

## P06-11I：重构 Writer 为无工具单轮写作流程（✅ 已完成 2026-08-19，本地 commit，未 push）

**任务目标**：根治 DeepSeek Writer 在 CrewAI 工具循环中反复调用 WriterContextReader、final 过短的历史问题——改为 Python 确定性加载两个 Pack → 构建紧凑上下文 → 一次无工具 LLM 调用 → ReportDraftAssembler 本地组装 → 现有质量门禁。

**新增文件**：
- `application/writer_context_builder.py`：`WriterContextBuilder/WriterContextLimits/BuiltWriterContext`——把 ResearchPack + FinancialAnalysisPack + CitationRegistry 压缩为单段写作消息；公司身份、写作规则与**全部合法 citation keys 永远保留**，facts/limitations/sources 超限时按条数与预算确定性裁剪（记录 dropped_sections）；输出低基数统计（context_chars/estimated_tokens/fact_count/source_count/citation_count/truncated）。
- `application/writer_direct_dispatch.py`：`WriterDirectDispatch` 端口 + `WriterDispatchResult/WriterDispatchError`——不传 tools/tool_choice/available_functions、不触发 beta.parse、重试复用同一份上下文。
- `infrastructure/direct_writer_dispatch.py`：`DirectLlmWriterDispatch`——普通 openai-compatible `chat.completions.create`，只读 `response.choices[0].message.content`（DeepSeek 普通正文）；可注入 mock client；`requests` 审计只记录模型/消息长度/has_error_summary，不含正文与密钥。
- `tests/test_p06_11i_writer_direct.py`：19 用例，覆盖 17 项验收重点。

**修改**：
- `infrastructure/flow_wiring.py`：`_exec_writer_stage` 对 DeepSeek/generic **不再创建 Writer Agent/Crew/执行 _kickoff_single**，直接走新的 `_exec_writer_stage_direct`；Qwen NATIVE_PYDANTIC 保留原 Crew 路径。direct 路径含至多一次 Writer-only 重试（空 content / finish_reason=length / 过短 / 缺章节），第二次失败稳定 LiveFlowExecutionError（REPORT_INVALID/REPORT_TRUNCATED）；新增 `direct_writer_factory` 注入点（测试零真实网络）；Jaeger span 增加 writer.context_build / writer.direct_llm / writer.assemble（只记 context_chars/estimated_tokens/fact_count/source_count/citation_count/output_chars/finish_reason/retry_count/status/error_code）。
- `infrastructure/observability/metrics.py` + `metrics_events.py`：新增低基数指标 writer_direct_requests_total{status}/writer_direct_duration_seconds{status}/writer_direct_output_chars{status}/writer_direct_retry_total{reason}。
- `tests/test_p06_11b_deepseek_structured_output.py` / `tests/test_p06_11e_deepseek_json_finalizer.py`：适配 `structured_output_mode(config, role)` 签名（P06-11G 变更遗留，补 role 参数不改测试目的）。

**验证**：`uv run python -m pytest` 新测试 19 passed + 相关回归 207 passed（flow_wiring/metrics/p06-11b~h/observability）；`uv run ruff check src` 全绿；`uv run python -m mypy src` 130 源文件全绿。

**关键决策**：
1. Writer 不再走 Agent/Crew 工具循环（根因：DeepSeek 在 max_iter 内反复调用 WriterContextReader，token 全耗在 tool_calls 参数上，final 仅 103~114 字符）；
2. 重新加载两个 Pack 是确定性 Python（`build_citation_registry` 唯一生成 source key），LLM 只输出 Markdown；
3. CitationRegistry 全部合法 key 永远完整保留（禁止静默截掉全部 registry），公司身份优先级最高；
4. 有限重试最多一次且只重试 Writer（复用同一份上下文 + 结构化错误摘要），不重新执行 Research/Analysis/外部工具、不切换模型、不伪造报告；
5. Jaeger span 与 Prometheus 只记录长度/数量/状态，绝不记录 prompt、正文、API Key。

**限制**：未执行真实 DeepSeek live 测试（遵守限制，等待授权）；Qwen NATIVE_PYDANTIC 原路径保留但未在真实 Qwen 下回归；Writer 上下文预算（max_chars=6000 等）为代码常量，可按需调参；本任务未同时重写 Research/Analysis。

**下一候选任务**：受控 MSFT fast live 验收（DeepSeek）：Docker 重建 api/worker 镜像 → 创建 fast 任务 → 确认 05_writer 生成完整报告、正文含合法 citation keys、writer_direct_* 指标出现、Jaeger writer.context_build/direct_llm/assemble span 出现且不含 prompt/正文；随后 P06-11（10 家公司效率对照实验）。

## P06-11K 系列收口（K-1~K-5 ✅，2026-08-20 全部完成）

**P06-11K-5（本窗口）**：生产接线补齐（worker `_build_live_component_factory`/`_build_live_components`/`_PerJobFlowRunner` 补齐 `diagnostics_provider`/`diagnostics_factory`/`set_job_id`）+ 所有诊断捕获点统一注入 OTel trace_id/span_id（flow_wiring `_diag_trace_ids`/`_diag_capture`、real_tools `_capture_tool_call`、llm_full_observer `_capture_llm_summary`，均走 tracing.py 新增公共 `current_trace_ids()`）+ tests/test_p06_11k5_fake_docker_injection.py（7 用例）+ docs/05/09/12 更新。

**验证（真实执行）**：
- `pytest tests/test_p06_11k5_fake_docker_injection.py -v` → 7 passed（工具摘要 request/response、cache 命中去重、预算耗尽只打参数、build_research_tools 签名、worker 注入点静态断言、fake Writer 失败诊断包脱敏+trace 一致、阶段顺序+ValidationError 落盘）
- `pytest tests/test_p06_11k1~k4 + test_p06_11j_observability + test_p06_11g_tool_span` → 69 passed
- `ruff check` 全绿（W292 已 --fix）；`mypy` 受检 6 文件无问题（research_flow.py:89 detach 为既有错误，按约束不修）

**限制**：未做真实 Docker live 故障注入验收（遵守限制，不执行 live/不 docker down）；worker 工厂注入点用源码文本静态断言（避免 import worker 触发 Celery app 构建 + DB 探活副作用）；fake 故障注入用 LiveResearchFlowRunner + fake crew + InMemorySpanExporter 隔离验证。

**下一候选**：P06-11（10 家公司效率对照实验）或 P06-12（完善 README 演示、架构图和限制）。
