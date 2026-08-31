# Agent 驱动的自动化投研系统

输入一家美国上市公司的名称或代码，系统自动检索 SEC 财报与公开信息，确定性计算财务指标，并生成**带可追溯引用、中英对照、成本可观测**的投研报告。

> ⚠️ **边界**：输出仅用于信息整理与技术演示，不构成投资建议。仅覆盖可在 SEC EDGAR 识别的美国上市公司。

---

## ✨ 核心能力

| 能力 | 说明 |
|---|---|
| **年度双期间流水线（P07）** | `annual_deep` 模式：目标/上一年 10-K ×2 + Company Facts 三路并发，DAG 节点编排 + 关键路径/恢复 |
| **确定性财务指标** | 10 项指标由 SEC XBRL 事实经 Decimal 确定性公式计算，**LLM 不算数、不改写** |
| **报告中文化** | 官方声明中英对照（LLM 忠实翻译 + 英文原文保留）、MD&A 中文摘译（≤500 字挑重点） |
| **引用契约** | 正文 `[来源名]` 标记 → 末尾来源清单可点击链接；数字与引用可对账（WS1） |
| **网页溯源（WS2）** | 分析师观点/评级机构搜索 + 网页快照缓存（`locator=snapshot:<key>`） |
| **成本可视化（WS3）** | 每次任务 token/估算成本落盘 → API → 前端"成本与用量" → Prometheus/Grafana |
| **质量评测（WS1）** | 内容保真对账（数字 vs pack）+ 引用可解析 + LLM 软评分（不进门禁） |
| **全链路可观测** | Prometheus 指标 + Grafana 看板 + Jaeger 分布式追踪 + 脱敏诊断包 |

## 🏗️ 架构总览

```
                 ┌──────────────┐
 用户 ──► Streamlit ──► FastAPI ──┼──► PostgreSQL（状态真相源）
            (HTTP client)   │     │──► Redis（Celery broker/Outbox）
                            │     └──► Celery Worker（Flow / 年度 Runtime）
                            │            ├─ SEC 下载 / XBRL / Serper 搜索
                            │            └─ DeepSeek LLM（有界、无工具）
                            │
          Prometheus ◄── api:8000/metrics  +  worker:9101（多进程聚合）
              ▲
          Grafana（看板）        Jaeger ◄── OTel 链路（API→Worker→LLM）
```

| 服务 | 技术 | 端口 |
|---|---|---|
| API | FastAPI + SQLAlchemy | `:8000` |
| Worker | Celery（prefork） | `:9101`（metrics） |
| 前端 | Streamlit | `:8501` |
| 数据库 | PostgreSQL 16 | — |
| 队列 | Redis 7 | — |
| 指标 | Prometheus | `:9090` |
| 看板 | Grafana | `:3000` |
| 追踪 | Jaeger + OpenTelemetry | `:16686` |

## 🚀 快速开始

```bash
# 1. 配置密钥（LLM/Serper），见 .env.example
cp .env.example .env

# 2. 启动全栈（API/Worker/DB/Redis/前端/观测）
docker compose up -d

# 3. 创建真实年度任务（deep 模式，真实 SEC + DeepSeek）
curl -X POST http://localhost:8000/v1/research-jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-amzn" \
  -d '{"input_company":"AMZN","as_of_date":"2025-10-31","language":"zh-CN",
       "requested_forms":["10-K"],"research_profile":"deep","research_mode":"annual_deep"}'

# 4. 查看状态 / 下载报告
curl http://localhost:8000/v1/research-jobs/<job_id>
curl http://localhost:8000/v1/research-jobs/<job_id>/artifacts/08_report.md
```

或打开 Streamlit `http://localhost:8501`：创建任务 → 轮询状态 → 查看报告与工件 → 下载诊断包。

## 💡 为什么这样设计（面试叙事）

> **确定性编排 + 有界 LLM + 引用契约 + 数字不信任 LLM + 可评测 + 可溯源 + 成本可控。**

1. **不把数值交给 LLM**：财务指标由 Python + Decimal 从 SEC XBRL 事实确定性计算；LLM 只负责解释、归因与写作（ADR-002）。
2. **有界的 Agent**：年度流水线由确定性 DAG 编排，每阶段 LLM 调用有明确输入/输出契约，不允许 Agent 自由调工具或无限反思。
3. **引用即契约**：`citation_keys` 是门禁；正文标记 → 来源清单可点击；`report_fidelity` 评测数字与引用可解析率。
4. **可评测**：三层基准判定（technical / live_acceptance / full_quality）+ 内容保真对账。
5. **成本可控**：每次任务 token/估算成本落盘并可视化（真实 AMZN ≈ $0.004/次，DeepSeek V4 Flash）。

## 📊 验证状态

- **全量离线回归：1564 passed / 32 skipped / 1 deselected**（唯一排除项为 Windows GBK CLI 已知问题）
- **真实 AMZN 年度报告**：SEC 10-K×2 + Company Facts → 三路并发 → 章节写作 → 发布（57 秒），含官方声明中英对照、MD&A 中文摘译、引用纯文本、来源清单去重
- ruff / mypy（170 文件）/ git diff --check 全绿
- 真实基准记录见 `docs/19`~`docs/23`、`docs/25`

## 🔭 观测体系

| 入口 | 地址 | 看什么 |
|---|---|---|
| Prometheus | `http://localhost:9090` | LLM/工具/Agent/任务/成本指标（PromQL） |
| Grafana | `http://localhost:3000`（admin/admin） | 8 行看板：任务、LLM、成本与用量(WS3) 等 |
| Jaeger | `http://localhost:16686` | API→Worker→LLM 全链路 trace |

Prometheus 打点原理、多进程聚合、如何加新指标 → [docs/29-PROMETHEUS-GUIDE.md](docs/29-PROMETHEUS-GUIDE.md)

## 📚 文档导航

| 文档 | 内容 |
|---|---|
| [产品需求](docs/01-PRD.md) / [技术架构](docs/02-ARCHITECTURE.md) | 需求与设计 |
| [年度双期间流水线 ADR](docs/24-P07-ANNUAL-PIPELINE-ADR.md) | P07 业务边界与决策 |
| [工作流可靠性与评估](docs/04-WORKFLOW-RELIABILITY.md) | 可靠性/评测口径 |
| [开发路线图](docs/05-DEVELOPMENT-ROADMAP.md) | 逐任务实施与勾选 |
| [运行指南](docs/10-RUN-GUIDE.md) | 本地/Compose/CLI 操作 |
| [项目术语](docs/21-PROJECT-GLOSSARY.md) / [端到端流程](docs/22-END-TO-END-REQUEST-FLOW.md) | 学习辅助 |
| [Prometheus 指南](docs/29-PROMETHEUS-GUIDE.md) / [Prometheus 事故复盘](docs/30-P06-13-PROMETHEUS-POSTMORTEM.md) | 观测使用/排障 |
| [缓存基础](docs/27-CACHE-BASICS.md) / [网络与数据库](docs/28-NETWORK-DB-BASICS.md) | 基础教学 |

## 🛠️ 技术栈

FastAPI · SQLAlchemy · Alembic · Celery · Redis · PostgreSQL 16 · Streamlit · CrewAI（legacy）· DeepSeek · Prometheus · Grafana · Jaeger · OpenTelemetry · Docker Compose · Ruff / mypy / pytest
