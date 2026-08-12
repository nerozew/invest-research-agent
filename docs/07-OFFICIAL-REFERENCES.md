# 官方资料与技术决策依据

访问日期：2026-08-11。实现某个任务时仍应再次检查对应官方文档，因为 API、模型名和小版本可能变化。

## 学习路线与项目定义

- AgentGuide 工程落地学习路线：<https://github.com/adongwanai/AgentGuide/blob/main/docs/05-roadmaps/learning-roadmap-development.md>

## CrewAI

- 官方文档首页：<https://docs.crewai.com/>
- Processes（sequential/hierarchical）：<https://docs.crewai.com/en/concepts/processes>
- Tasks（context、output file、Pydantic/JSON output、guardrail）：<https://docs.crewai.com/en/concepts/tasks>
- Flows（state、event-driven workflow、branching）：<https://docs.crewai.com/en/concepts/flows>

本设计据此采用 `Process.sequential` 串联三项 Agent Task，并以 Flow 管理持久状态、条件分支和恢复。Task 输出使用 Pydantic schema、guardrail 和显式 context。

## SEC EDGAR

- EDGAR 数据 API：<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- SEC Developer Resources 与公平访问：<https://www.sec.gov/about/developer-resources>
- Webmaster FAQ（User-Agent、当前 10 requests/second 指引）：<https://www.sec.gov/about/webmaster-frequently-asked-questions>

`data.sec.gov` 的 public submissions 和 XBRL Company Facts 无需 API key。项目仍必须声明 User-Agent、缓存响应并限制速率。不要把公开数据 API 与用于提交申报的 EDGAR Next filer API 混为一谈。

## DeepSeek

- API 快速开始：<https://api-docs.deepseek.com/>

DeepSeek 当前提供 OpenAI/Anthropic 兼容接口。模型名和能力会变化，因此应用通过环境变量配置模型并在每次 run manifest 记录实际模型，不把模型名散落在业务代码里。

## Cline

- Plan & Act：<https://docs.cline.bot/core-workflows/plan-and-act>
- Cline Rules：<https://docs.cline.bot/customization/cline-rules>
- Commands/Workflows：<https://docs.cline.bot/core-workflows/using-commands>

本项目把长期工程规则放在 `.clinerules/`，把可复用的小任务流程放在 `.clinerules/workflows/next-task.md`，大任务先在 Plan 或 `/deep-planning` 中设计再进入 Act。

## 未来可选升级：Azure（不属于当前项目范围）

- FastAPI 部署到 Azure Container Apps：<https://learn.microsoft.com/en-us/azure/developer/python/tutorial-containerize-simple-web-app>
- Container Apps 微服务参考架构（Key Vault、Managed Identity、Monitor）：<https://learn.microsoft.com/en-us/azure/architecture/example-scenario/serverless/microservices-with-container-apps>
- Azure Database for PostgreSQL 概览：<https://learn.microsoft.com/en-us/azure/postgresql/overview>
- PostgreSQL Flexible Server 高可用：<https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-high-availability>
- PostgreSQL pgvector：<https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-use-pgvector>
- Blob Storage Python quickstart：<https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python>
- Azure Monitor OpenTelemetry Python/FastAPI：<https://learn.microsoft.com/en-us/troubleshoot/azure/azure-monitor/app-insights/telemetry/opentelemetry-troubleshooting-python>

这些链接仅供项目完成后评估云端升级时参考。当前开发路线不创建 Azure 账户、不部署 Azure 资源、不安装 Azure SDK，也不把 Azure 作为功能、验收或简历成果。未来如果决定升级，应另写 ADR、成本预算和独立任务路线后再实施。
