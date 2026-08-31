# 下一窗口交接提示词：P06-11K-4（复制本文件内容到新窗口）

> 把下面「交接提示词」整段复制到新 Cline 会话的第一个消息即可继续。

```
【P06-11K-4：LLM/工具/SEC/Serper/Calculator 调用信息 + trace 关联 + API 安全下载 + 前端失败详情页】

项目路径：d:\MyProjects\invest_research_agent
模式：ACT MODE。目标：实现 P06-11K 系列的第 4 个子任务（P06-11K-4）。
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
  测试：tests/test_p06_11k1/11k2/11k3 共 39 用例通过。当前 HEAD=96d00b0。

## P06-11K-4 目标（对应原始任务文档第七/八节与测试 15/16/17）
1. LLM/CrewAI 工具/SEC/Serper/Calculator 调用信息：
   - LLM 调用结果类型区分 content / tool_calls / empty（writer_direct_dispatch 已有 WriterDispatchResult，
     finalizer 的 DeepSeekJsonObjectFinalizer 有 finish_reason/content；llm_call_observer 已有事件总线）；
   - 工具调用参数脱敏后摘要（SEC submissions/companyfacts、Serper 在 infrastructure/real_tools.py /
     infra/prefetch.py，工具参数进 redaction 后入 capture）；
   - SEC/Serper 请求与结果摘要（来源 URL/accession number/locator/标题/结果数量/有界文本预览）。
2. trace 关联：每条 DiagnosticEvent 已带 trace_id/span_id（capture 传 trace_id/span_id）；
   Jaeger Span 只增加低基数属性：diagnostic.event_id、diagnostic.available=true、payload_kind、
   input_size/output_size、validation_error_count；禁止把完整 Payload 塞入 Span。
   Span 补丁位置参考 infra/observability/tracing.py 的 span() 与 stage_tracing.py 的 stage_span()。
3. API：复用 artifacts.py 的安全下载机制（application/artifacts.py 的 _validate_artifact_key 防路径穿越、
   GetJobArtifactsService/GetJobArtifactContentService；infrastructure 实现 ArtifactContentStore）。
   新增「下载脱敏诊断包」端点：不存在诊断包时返回明确提示（不 500）；路径穿越返回 4xx。
   失败任务详情 DTO 增加：failure_stage、error_code、（最近 10 条执行事件可读自 diagnostics/execution_timeline.jsonl）。
4. 前端：frontend/pages/3_报告与工件.py 或 1_创建投研任务.py 的失败详情增加：
   错误阶段、稳定错误码、最近 10 条执行事件、「下载脱敏诊断包」按钮 + 「诊断包可能包含业务输入，仅供本地调试」提示。
   前端只调 FastAPI，不在 session state 存密钥。

## 测试清单（tests/test_p06_11k4_*.py，至少覆盖任务文档第九节）
15. API 下载防路径穿越（非 UUID job_id、..、绝对路径均拒绝）；
16. 前端失败任务能看到诊断入口（fake HTTP API 测试）；
17. Jaeger/Prometheus 不包含完整 Payload（检查 span 属性白名单 + 指标 label 无 payload）。
另加：工具参数脱敏后可查看（SEC/Serper/Llm 摘要经 redaction）；SEC/Serper 请求/结果摘要捕获点单测。

## 实施顺序建议
1. 先调研：real_tools.py（SEC/Serper 工具）、prefetch.py、llm_call_observer.py、fastapi 路由文件、artifacts 下载实现、frontend 失败页。
2. 应用层：diagnostics 增加「工具/SEC/Serper 请求摘要构造器」（纯函数，脱敏后入 capture）。
3. flow_wiring/real_tools 接线：在工具执行包装层捕获脱敏摘要（注意 tool_cache 命中时不重复捕获）。
4. Jaeger span 属性补丁（只加白名单低基数属性）→ 测试 17。
5. FastAPI 端点（复用 artifacts 安全下载）→ 测试 15。
6. 前端失败详情页 → 测试 16。
7. docs/05 把 P06-11K-4 行标 ✅；独立 commit；不 push。

## 硬性约束
- 只做 P06-11K-4 一个任务；完成后停止汇报，等待用户确认，不自动进入 K-5。
- 每个子任务独立 commit；git add 只用显式路径（工作区有大量用户未提交修改，勿误加）。
- 不执行 live、不修改真实 .env、不执行 docker compose down -v、不 push。
- 针对性 pytest + Ruff + mypy；只汇报真实执行的命令与真实结果。
- reasoning_content/API Key/Authorization/Cookie 永远不得保存进诊断包。
- 全 src mypy 的 flows/research_flow.py:89「detach」错误是既有问题，与 K 系列无关，不顺手修。
```

（交接提示词结束）