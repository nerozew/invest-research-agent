# P06-11J：Agent/CrewAI 完整调用链可观测性收口 — 审计结论

> 状态：P06-11J（仅完善可观测性）。审计时间：2026-08-19。
> 遵守：不执行真实 DeepSeek/SEC/Serper；不实施 P06-11I Writer 重构；不修改真实 .env；不 push。

## 一、为什么当前 Jaeger 只有 api.request → worker.process → flow.run

三个顶层 span 恰好覆盖 HTTP 入口 → Worker 任务 → Flow 总入口，但中间没有任何子 span：
- `api.request`：Starlette HTTP middleware（api/app.py）；
- `worker.process`：Celery task 包装。
- `flow.run`：`LiveResearchFlowRunner._run_live`（flow_wiring.py:683）。

`flow.run` 内部所有真实工作（阶段执行、LLM 调用、工具调用、质量门禁、组装）都没有被
`start_as_current_span` 包裹为子 span，导致 Jaeger 只有一根"裸树干"，无法定位单任务细节。

## 二、六项重点审计结论

### 1. Agent Span 只在执行结束后补记（确认）

- `_record_agent_metrics()`（flow_wiring.py:1781）是唯一创建 `agent.{role}` span 的地方，
  且只在**兼容路径**（注入 fake crew、一次 kickoff）的 kickoff **完成后**被调用
  （`_run_live_impl` L732）。分阶段路径（`_run_staged`）**从不调用**它（L735 置 None）。
- 它创建的 span 在**当前时间**才开始（未传 start_time），虽然计算了 `duration_s` 属性，
  但 span 生命周期是零耗时外壳 —— 属于任务执行结束后补记的"补记 Span"，不是
  `start_as_current_span` 包裹真实执行边界。
- 若 task.start_time/end_time 缺失（异常），整个 agent span 被跳过。

### 2. LLM Span 在 Completed 时才创建，父 Context 丢失（确认）

- `LlmCallObserver._on_started`（llm_call_observer.py:252）只保存双时钟起点，**不创建 span handle**；
- `_record_span`（L398）在 `_on_completed`/`_on_failed` 时才 `tracer.start_span("llm.request", ...)`。
- `start_span` 在**当前 context** 下创建。Completed/Failed 事件在模型调用返回后 emit，
  此时 OTel context 已回到 `flow.run`（且内部没有 agent 子 span），因此 llm.request
  不是嵌套在"Agent 执行期间"的子 span —— 父 Context 与真实调用位置脱节。
- 此外 `_started_at` 以 `(role, model)` 为键：同一 Agent 工具循环中同 role+model 的
  多次 LLM 调用会**相互覆盖**起点，导致 duration 错配、Completed 的 pop 拿到错误条目、
  键残留泄漏。

### 3. 项目 real_tools Span 覆盖不到 CrewAI ToolUsage 事件（确认）

- `_tool_span`（real_tools.py:200）只包裹项目**直接调用**的工具函数
  （sec_submissions / web_search / sec_company_facts / document_parser / artifact_writer）。
- CrewAI Agent 工具循环 emit 的 `ToolUsageStartedEvent / ToolUsageFinishedEvent /
  ToolUsageErrorEvent`（tool_usage_events.py:55-75）项目从未订阅 →
  Agent 工具循环（含 WriterContextReader/ArtifactLoader）在 Jaeger 完全不可见。

### 4. agent_id/agent_role/task_id 映射导致事件被丢弃（确认，严重）

- `_subscribe_llm_calls`（flow_wiring.py:1647）用 `zip(_AGENT_ROLE_ORDER, crew.agents)`
  建 agent_id→role 映射。**分阶段**路径每次 `_kickoff_single` 只构建单 Agent Crew，
  zip 后该 Agent 的 id 总被映射成第一个角色 `research`，analysis/writer 阶段若事件
  带 agent_id 会命中**错误映射** → `_event_role` 返回错误角色或被丢弃。
- `LLMCallFailedEvent`（llm_events.py:62-66）**没有 model 字段**，`_on_failed` 用
  `_latest_model(role)` 回退，同一 role 多次 Started 未完成时可能配对错误。
- scoped_handlers 的 `scope.__exit__` 只注销事件 handler，**不清理** `_started_at`
  残留键 → 跨 Job 泄漏。

### 5. BatchSpanProcessor 未在 Worker 子进程退出前 flush（确认）

- worker.py `_setup_otel_from_env`（L413）配置 endpoint → OTLP **BatchSpanProcessor**
  （schedule_delay_ms=5000）。
- 无任何 `force_flush` 钩子；`worker_process_shutdown` 信号只清理 metrics .db
  （worker.py:489-490），不 flush trace。Celery prefork 子进程退出依赖 atexit，
  使用 `os._exit` 时不一定触发 → span 丢失。

### 6. Prefork 子进程未分别正确初始化 TracerProvider（确认）

- `setup_tracing` 在 `_build_celery_app()`（worker.py:499）**父进程**模块导入时执行一次。
- Celery prefork 子进程 fork 继承父进程 TracerProvider 与 BatchSpanProcessor——
  其导出线程在 fork 时**不存在**（父进程导出线程不会被复制），子进程没有活跃导出线程，
  所有 span 永远不进队列上传。必须让每个子进程 `worker_init` 内重新初始化。

## 三、修复方向（与任务要求一致）

1. 在真实执行边界使用 `start_as_current_span` 包裹 11 类阶段 span，禁止补记；
2. LLM span 改为 Started 创建 handle → Completed/Failed 结束**同一个** span，
   用稳定序号/队列配对，禁止 (role,model) 覆盖；
3. 新增 CrewAI ToolUsage 事件订阅，生成 `crewai.tool.<stable_name>` span + 指标；
4. 修复 agent_id 映射（分阶段直接用单 Agent 的 role，不回退 zip）；
5. Prefork 子进程 worker_init 重新 setup_tracing + worker_process_shutdown force_flush；
6. 新增 Job-local ExecutionTimelineSink（10_execution_timeline.jsonl，写出失败不影响业务）；
7. 新增低基数 Prometheus 指标 + Grafana 面板。