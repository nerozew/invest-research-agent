# 下一窗口交接提示词：P06-11K-5（复制本文件内容到新窗口）

> 把下面「交接提示词」整段复制到新 Cline 会话的第一个消息即可继续。

```
【P06-11K-5：fake Docker 故障注入 + 文档收尾】

项目路径：d:\MyProjects\invest_research_agent
模式：ACT MODE。目标：实现 P06-11K 系列的第 5 个子任务（P06-11K-5，最后一个）。
必须遵守 docs/05-DEVELOPMENT-ROADMAP.md 中「1. 使用规则」「2. 每个任务的固定交付格式」，
以及 .clinerules/01-project.md、02-engineering.md、03-learning-mode.md。

## 已完成的系列状态（勿重做）
- P06-11K-1 ✅ commit 295611e：settings.py/.env.example 诊断 5 配置（默认 off + production all_payload fail-fast）；
  diagnostics/{models,redaction,sink,__init__}.py（DiagnosticEvent/ValidationErrorEntry/DiagnosticCapturePolicy、
  递归脱敏（reasoning/CoT 永不保存）、DiagnosticCaptureSink Protocol + BoundedDiagnosticBuffer Ring Buffer）。
- P06-11K-2 ✅ commit 69d0f4b：diagnostics/persistence.py（DiagnosticManifest/DiagnosticBundleFiles/build_bundle_files
  落盘语义）+ infrastructure/diagnostics_bundle_store.py（resolve_diagnostics_dir UUID 防穿越、
  DiagnosticsBundleStore 原子写 mkstemp/fsync/os.replace + write_if_absent 幂等 + DiagnosticsCleanupService 过期清理）。
- P06-11K-3 ✅ commit 96d00b0：diagnostics/capture.py（DiagnosticCapture 先脱敏再入缓冲）+ flow_wiring.py
  （run() try/except 收口 + _finalize_diagnostics_failure/success + _persist_diagnostics_bundle 失败落盘 +
  research_inputs/ResearchPack/analysis_inputs/FinancialAnalysisPack/writer_context/writer_response_*/
  ReportDraft/quality_report/revision_result 捕获点 + build_flow_runner diagnostics_factory 透传）。
- P06-11K-4 ✅ commit ea56a55：LLM/工具/SEC/Serper 脱敏摘要 + Jaeger 低基数属性 + 诊断包安全下载 + 前端失败诊断页。
  新增 application/diagnostics/tool_summaries.py（工具参数白名单摘要 + SEC/Serper 结果结构化摘要 + LLM 三态摘要）；
  real_tools._cached_execute/_capture_tool_call（cache 命中不重复捕获结果）；llm_full_observer（LLM 三态摘要入诊断包 +
  Jaeger Span 只加 diagnostic.* 白名单属性）；api/app.py（GET /v1/research-jobs/{job_id}/diagnostics 下载 tar.gz，
  UUID 防穿越、不存在 404）；frontend（render_failed_diagnostics + build_diagnostics_event_rows 最近 10 条 + 下载按钮）；
  tests/test_p06_11k4_{api_download,frontend_diagnostics,obs_payload_redaction}.py 共 21 用例通过。
  docs/05 中 K-1~K-4 已标 ✅，K-5 保持未标。当前 HEAD=ea56a55（分支 agent/m2-deterministic-tools）。

## P06-11K-5 目标（对应 docs/05 第 246 行；这是系列最后一步）
「fake Docker 故障注入 + 文档收尾」：
1. 生产接线补齐（K-4 遗留，必须先做）：
   - worker.py 的 _build_live_component_factory / _build_live_components 调用 build_research_tools 时
     把 diagnostics_provider 传入（工具摘要捕获在生产路径生效）；build_flow_runner 的
     diagnostics_factory 从 worker 侧由 diagnostics_factory 产出 Job-local capture 的惰性闭包接入；
   - 确认 llm_full_observer 的 _diagnostics_provider 生产接线（run() 内刷新 + _subscribe_llm_calls 透传）已生效。
2. fake Docker 故障注入全链路验收（FLOW_MODE=fake）：
   - 注入 Writer Schema 失败（例如 fake LLM 返回缺必需字段 / 非 ReportDraft 结构），让任务到达 failed；
   - 前端失败详情页显示 failure_stage、error_code、最近 10 条执行事件、可下载脱敏诊断包；
   - 诊断包内顺序可见 Request→ResearchPack→AnalysisPack→Writer Context→Writer Response→Validation Error；
   - 敏感字段已脱敏（reasoning_content/API Key/Authorization/Cookie 绝不在包内）；
   - Jaeger trace_id 与该任务所有事件一致；无真实 DeepSeek/SEC/Serper 调用（fake 隔离栈）。
3. 文档收尾：docs/05（P06-11K-5 标 ✅）、docs/09-LEARNING-LOG.md、docs/12-PHASE6-HANDOFF.md 按实际结果更新；
   禁止声称任务成功率 >95% 或效率提升 XX%（.clinerules/01-project 硬性约束）。

## 测试清单（tests/test_p06_11k5_*.py，至少覆盖）
- 生产接线单测：build_research_tools(diagnostics_provider=...) 注入后真实工具路径产生 tool_request/tool_response
  摘要事件（cache 命中只补参数、结果不重复捕获）；worker component_factory 构造的 research_tools 已携带 provider。
- fake Docker 故障注入验收：全链路失败任务诊断包下载、字段顺序、敏感字段脱敏、trace_id 一致（隔离 fake 栈）。
- 完整相关回归全绿：K-1~K-4 全部测试 + flow_wiring + observability + real_tools 相关测试。

## 实施顺序建议
1. 先调研：worker.py 的 _build_live_component_factory / _build_live_components、build_flow_runner、
   flow_wiring run() 的 diagnostics_factory 生命周期、fake Docker 验收脚本（参考 docs/13B-P06-11G-NEXT-WINDOW.md
   的 docker compose 命令）、现有 e2e fake 测试（tests/test_e2e_fake*.py、test_final_report_artifacts.py）。
2. 补齐生产接线（worker/flow_wiring/real_tools 的 diagnostics_provider 贯通）→ 单测。
3. fake Docker 故障注入全链路：按 K-3 测试模式注入 fake Writer 失败，验证任务 failed + 诊断包 + 前端入口。
4. 文档收尾：docs/05 标 ✅ + docs/09 学习日志按固定格式补一条 + docs/12 更新 K 系列完成状态。
5. 独立 commit；不 push。

## 硬性约束
- 只做 P06-11K-5 一个任务；完成后停止汇报，等待用户确认。这是 K 系列最后一项，不再有 K-6。
- 每个子任务独立 commit；git add 只用显式路径（工作区有大量用户未提交修改，勿误加）。
- 不执行真实 live、不修改真实 .env、不执行 docker compose down -v、不 push。
- 针对性 pytest + Ruff + mypy；只汇报真实执行的命令与真实结果。
- reasoning_content/API Key/Authorization/Cookie 永远不得保存进诊断包。
- 全 src mypy 的 flows/research_flow.py:89「detach」错误是既有问题，与 K 系列无关，不顺手修。
```

（交接提示词结束）