# P06-11G 下一个窗口交接提示词（当前改动未 push，待观测重跑）

## 一句话现状
LLM 角色解耦（注册表+引用+per-role thinking）**代码已完成并通过静态校验**；
真实 fast 重跑（deepseek，Writer thinking=true、Research/Analysis=false）仍是 `REPORT_INVALID` / 05_writer / 32.5s，
根因定位为 **deepseek-v4-flash "长文在工具循环中间(875 tokens)、final answer 只有 23 tokens(≈114 字符)"**，与思考开关无关。

## 交给你继续的提示词（可直接复制）
> 【P06-11G 观测重跑】读取 `docs/12-PHASE6-HANDOFF.md` 末尾 P06-11G 增补与 `docs/` 本交接文件。
> 已完成：LLM 角色解耦（settings.py ModelProfile/RoleLLMOverride/LLM_MODELS/LLM_ROLE_*/build_role_llm_config；
> llm_factory.py RoleLLMConfig/config_for/structured_output_mode(config,role)；flow_wiring fast 只关 Research/Analysis thinking；
> analysis_task/writer_task/finalizer 按角色判定）。mypy 126 源文件、ruff 全绿、P06-11G 测试 5 passed。
> 本轮已加观测点：`flow_wiring._assemble_writer_markdown` 失败时打 info 日志（final_len/recovered_len/recovered）并
> 把 `agent.last_messages` + final answer + 回收候选落盘为 `<artifact_root>/<job_id>/05_writer_tool_history.txt`。
> 请：1) 重建 api/worker 镜像并 `--force-recreate`（worker concurrency=1 临时）；2) 用当前 .env（deepseek，
> LLM_WRITER_ENABLE_THINKING=true、R/A=false）重跑一次 MSFT fast（每任务仅一次）；3) 读该 job 的
> `05_writer_tool_history.txt` + worker 日志，确认"回收为什么失败/长文在哪一步被丢弃"；4) 未确认根因前不要换模型。

## 关键命令
- 重建：`docker compose --project-name agent build api worker`
- 重建容器：`docker compose --project-name agent up -d --force-recreate api worker`（worker command 需临时加 `--concurrency=1`；验收后还原）
- 查看 trace：Jaeger http://localhost:16686（service=invest-research-worker；已知缺口：flow.run 下无 stage 子 span）
- 日志：`docker logs agent-worker-1 --since 10m`
- 提交/轮询任务参考：`tmp/p06_11g_live_submit.py`（已删，可重建；POST /v1/research-jobs body 含 input_company/MSFT, as_of_date/2025-10-31, requested_forms/["10-K"], research_profile/fast）

## 已确认的证据（不要再重复重跑验证）
- MSFT job 10a560de（celery b198fa2f）：32.7s，04_analysis succeeded，05_writer REPORT_INVALID。
- Worker 日志时序：SEC submissions/companyfacts/Serper 各 200 → deepseek×6（research finalizer 2122 tokens、
  analysis 145/679、writer 中间 875、writer final 23）→ RuntimeError('Writer 输出过短（114 字符）')。
- Jaeger 该 trace：api.request(196ms)→worker.process(32.8s)→flow.run(32.5s)，无内部子 span（可观测性缺口）。
- Prometheus/Grafana 只有聚合（failed 耗时/llm_requests/tool_calls），不能定位单任务细节。

## 待确认的根因分支（下一步阅读 05_writer_tool_history.txt 后判断）
A. `_longest_writer_history` 返回 None（last_messages 无 assistant 长文）→ 说明 CrewAI 未保留或字段不同，需改读取方式；
B. 返回了 875 tokens 长文但 `ReportDraftAssembler` 仍 reject（缺章节/引用）→ 说明回收可救 final 但质量仍不足；
C. 返回与 final 相同 → 说明最后一步已被覆盖，需在 kickoff 侧保留历史。

## ✅ 已确认根因（2026-08-19 重跑观测，job ab7e6b5b）
- MSFT fast 重跑（deepseek，Writer thinking=true、R/A=false）仍是 REPORT_INVALID / 05_writer / 56.9s。
- Worker 日志：`writer history: no assistant text candidate (messages=3)`、
  `writer reassemble: final_len=114 recovered_len=None recovered=False`。
- `05_writer_tool_history.txt`（221 字节）3 条消息全部 `role=? len=0`，final answer 114 字符
  （"I need to read the context packs first..."）。
- **根因 = 分支 A（更精确）**：CrewAI 1.6.1 的 `agent.last_messages` 返回
  `list[LLMMessage]`，而 `LLMMessage` 是 **TypedDict（字典）**（`core.py:122/1352`，
  `utilities/types.py:8`），字段为 `role`/`content`。
  但 `_longest_writer_history`/`_dump_writer_tool_history` 用 `getattr(msg, "role"/"content")`
  访问字典 → 全部回退默认值 → 所有消息被跳过 → 回收返回 None。
  **长文其实存在**（messages=3 有内容），只是读取方式错误。
- **结论：不需要换模型**。修复方向：`_longest_writer_history`/`_dump_writer_tool_history`
  改用双兼容读取（`msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)`），
  并补一条单元测试（fake agent 的 last_messages 用 dict 形态验证回收命中）。

## ✅ 修复后重跑观测（2026-08-19，commit c88c4b9，job 3b83d36c）→ 判定分支 C
- **修复已生效**：`05_writer_tool_history.txt`（8258 字节）角色/长度正确显示——
  `[0] role=system len=3257`、`[1] role=user len=1354`、`[2] role=assistant len=114`。
  证明 `_message_role`/`_message_text` 的 dict 兼容读取修复正确。
- **但仍失败**：`writer reassemble: final_len=114 recovered_len=114 recovered=False`，
  任务仍 REPORT_INVALID / 05_writer。回收候选与 final answer **完全相同**（114 字符）。
- **根因 = 分支 C（回收候选与 final 相同）的实证**：`agent.last_messages`
  （= `agent_executor.messages.copy()`，crew_agent_executor.py:134/177/178/365 append）
  在 Writer 完成后只保留 **system + user + 最后一次 assistant（114 字符）** 三条。
  Writer 阶段的多次 deepseek 调用（completion 148/666/917/23 tokens）中的
  **工具循环中间长文从未出现在 last_messages**——它可能在 handle_agent_action_core /
  process_llm_response 路径被消费后未回灌 messages，或被后续覆盖。
  `_invoke_loop`（crew_agent_executor.py:207）每轮 `_append_message(formatted_answer.text)`
  只追加解析后的 answer 文本，非模型原始长输出。
- **结论：`agent.last_messages` 不是可回收长文的数据源**。继续修读取方式无效。
- **下一轮修复方向（未执行）**：在 kickoff/LLM 调用侧拦截真实响应——利用
  `LlmCallObserver` 或自定义 LLM callback，把 Writer 每轮模型实际输出保存到
  job-local 存储；final answer 过短时从该存储回收最长正文。这正是交接分支 A
  提示的"需在 kickoff 侧保留历史"。也可评估 `WriterContextReader` 返回的
  pack 是否过长导致模型在 context 溢出后只输出简短 final（需再观测）。

## 未 push / 未改模型 / 未进基准
未 push（commit c88c4b9 仅本地）、未测试阿里、未 10 家基准；.env 当前为 deepseek
全局 + per-role thinking 覆盖；compose.yml 已还原 worker command（无 concurrency 覆盖）。
真实 MSFT fast 已跑 2 次（每次均失败，最后一次是修复后验收），不再重复付费调用。
