# 当前任务交接（Task Handoff）——live 部署修复续

> 本文件用于在**新窗口/新会话**继续当前工作：先读本文件 + docs/13-ERRORS-REVIEW.md，再按下面提示词继续。
> 更新时间：Ubuntu live 任务失败修复完成后。

---

## 一、当前任务背景

Ubuntu 服务器（admin_ze@192.168.203.129，hostname milnus）已部署 compose 栈（postgres/redis/api/worker/streamlit healthy，镜像 ghcr.io/nerozew/invest-research-agent:phase5）。重跑 live 任务时失败：

- 任务 051a3cb6-fb93-4a42-b619-31ebdc5147c5：Analysis Agent 输出空 facts，撞 schema（List should have at least 1 item），CrewAI 在 kickoff 内校验抛错 → 任务状态永久卡 running。
- 服务器 .env：FLOW_MODE=live、RESEARCH_PROFILE=fast、LLM_ENABLE_THINKING=false、模型 research/analysis=qwen3.5-flash、writer=qwen3.5-plus。

## 二、已完成（本轮 5 项修复，commit b06efb0）

1. src/invest_research/domain/models.py：FinancialAnalysisPack.facts 改 default_factory=list 允许空（P05.5-deploy-fix 注释）。
2. tests/test_models.py：test_financial_analysis_pack_requires_facts → test_financial_analysis_pack_allows_empty_facts。
3. src/invest_research/application/execution.py：ExecutionStatusWriter 协议增加 mark_failed；ExecuteResearchJobService.process 用 try/except 包 flow_runner.run → 异常时 mark_failed 后 re-raise。
4. tests/test_execution_service.py：FakeWriter 增加 marked_failed/mark_failed + import pytest + 新测试 test_flow_failure_marks_failed_and_reraises。
5. src/invest_research/infrastructure/queue/worker.py：_RepoWriter.mark_failed（running→failed + completed_at 条件更新）。

**验证结果**：pytest 71 passed（test_models/test_execution_service/test_flow_adapter/test_application_stores/test_api_artifacts/test_api_get_job/test_execution_recorder），ruff 全过，mypy 107 文件无问题。commit b06efb0 已提交到分支 agent/m2-deterministic-tools。

## 三、待办（下一步）

### 1. 推送（被网络打断，未完成）

- git push origin agent/m2-deterministic-tools 尚未成功（GitHub 443 间歇性 Connection reset，已知故障）；
- 处理：重试 push（可间隔 15s 重试多次）；成功后确认 origin HEAD=b06efb0。

### 2. 服务器重建镜像并验证 live 任务（需要用户配合/授权）

- 代码改动必须重建镜像：push → GitHub Actions 构建 → 服务器 docker pull ghcr.io/nerozew/invest-research-agent:phase5 → docker compose up -d --pull always；
- 重跑 live 任务验证：预期空 facts 也能 published（报告如实标注“无财务事实”）；失败任务应显示 failed 而非卡 running；
- 验证新 worker 的 workflow_steps/artifacts/耗时落库（ExecutionRecorder）。

### 3. 后续数据流缺口（已提出、未拍板，非本次验收阻塞）

- Analysis 拿不到真实 XBRL 事实：ResearchPack 不带 facts、Analysis 工具白名单无 SEC 工具；
- 可选方案：prefetch 阶段取 SECCompanyFacts 并注入 analysis inputs（让报告有真实财务指标）。

## 四、环境备忘

- 分支 agent/m2-deterministic-tools，本地 HEAD=b06efb0（未推送）；origin 此前到 6604c3f。
- 沙箱测试命令：.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider；ruff：.venv\Scripts\python.exe -m ruff check src tests；mypy：.venv\Scripts\python.exe -m mypy src。
- 工具链：中文路径 write/edit 偶发 ReplaceFileW EIO（重试或 read 全量 + write 重写）；tools.read 单次有行数上限，长文件分段读。
- 约束：不修改 .venv/site-packages；不用 os._exit/sleep/skip 掩盖问题；live 付费测试须用户明确授权；推送已授权。

---

## 五、给下一个窗口的提示词（复制即用）

```text
继续投研项目 live 部署修复工作（分支 agent/m2-deterministic-tools，工作目录 D:\MyProjects\agent驱动的自动化投研系统）。

背景：Ubuntu 服务器 live 任务因 Analysis 空 facts 撞 schema 卡 running，修复已完成并提交（commit b06efb0：允许空 facts + 执行服务 mark_failed 兜底 + worker _RepoWriter.mark_failed），pytest 71 passed、ruff/mypy 干净。

任务：
1. 检查 git push origin agent/m2-deterministic-tools 是否成功；未成功则重试（GitHub 443 间歇性故障，间隔 15s 重试最多 5 次），成功后确认 origin 与本地一致（b06efb0）。
2. push 成功后告知用户：在 Ubuntu 服务器（admin_ze@192.168.203.129）执行 docker pull ghcr.io/nerozew/invest-research-agent:phase5 && docker compose up -d --pull always 重建镜像，然后重跑 live 任务验证：空 facts 任务应 published（报告标注无财务事实）、失败任务应显示 failed 不再卡 running、workflow_steps/artifacts/耗时应落库。
3. 用户授权后重跑 live 测试并汇报结果；不要自行跑付费 live 测试。
4. 可选后续（用户拍板再做）：补 Analysis 数据流缺口——prefetch SECCompanyFacts 并注入 analysis inputs，让报告有真实财务指标。

详细错误记录见 docs/13-ERRORS-REVIEW.md，交接细节见本文件。
```
