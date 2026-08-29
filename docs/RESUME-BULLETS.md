# 简历项目描述 v1（RESUME-BULLETS）

> 只使用**已测量**的数字；每个数字都能追溯到测试/基准/真实运行记录。
> 面试讲述：突出"为什么不让 Agent 自由干"的完整思考闭环。

## 一句话项目定位

**确定性编排 + 有界 LLM 的自动化美股投研系统**：输入公司名/代码，自动检索 SEC 财报与公开信息，确定性计算财务指标，生成带可追溯引用、中英对照、成本可观测的投研报告。

## 简历条目（可粘贴）

### 条目 A：系统能力（Tech Lead / 全栈 AI 工程师）

> 构建 Agent 驱动的美股投研自动化系统（FastAPI + Celery + PostgreSQL + DeepSeek），
> 用**确定性 DAG 编排 + 有界 LLM** 替代"三个 Agent 自由协商"：财务指标由 SEC XBRL
> 事实经 Decimal 公式计算（**LLM 不算数**），LLM 仅负责解释/归因/写作。年度流水线
> 支持目标/上一年 10-K ×2 + Company Facts 三路并发、节点级恢复与关键路径追踪。
> **真实 AMZN 年度报告 57 秒发布**，含官方声明中英对照、MD&A 中文摘译、可点击来源清单。

### 条目 B：质量与评测（可量化）

> 建立三层基准判定（technical / live_acceptance / full_quality）+ 内容保真对账
> （报告数字 vs 指标包 Decimal 精确匹配）与引用可解析率评测。
> **全量离线回归 1564 passed / 32 skipped**；ruff / mypy（170 文件）全绿。
> 真实 SEC/Serper/DeepSeek 端到端验证通过，报告 312 个可点击引用链接（222 SEC + 90 web）。

### 条目 C：成本可观测（可量化）

> 实现每任务 token/成本落盘与可视化：API 带出 `performance`、前端"成本与用量"表、
> Prometheus 聚合指标 + Grafana 看板。**真实 AMZN 年度任务 ≈ $0.004/次**
> （DeepSeek V4 Flash，输入 $0.14/百万 + 输出 $0.28/百万）。

### 条目 D：工程化与可观测性

> 全链路可观测：Prometheus 指标（30+ 指标）、Grafana 8 行看板、Jaeger 分布式追踪、
> 脱敏诊断包下载。解决 Celery prefork 多进程指标聚合、OpenTelemetry fork 后 span
> 丢失等生产问题。事务性 Outbox、幂等 API、stale-job 恢复、故障注入测试齐备。

## 面试叙事主线

1. **问题**：Agent 自由协商 → 数值不可信、成本不可控、失败不可解释。
2. **方案**：
   - **确定性优先**：指标用 Python 算（ADR-002），LLM 只在有界契约内解释/写作。
   - **引用即契约**：citation_keys 门禁 + 来源清单可点击 + 数字对账评测。
   - **可评测**：三层基准 + 内容保真 + 引用可解析率。
   - **可观测**：成本/token 落盘 + Prometheus/Grafana/Jaeger。
3. **结果**：真实 AMZN 57 秒出报告、$0.004/次、1564 passed。

## 支撑证据索引

| 数字 | 来源 |
|---|---|
| 1564 passed / 32 skipped | 全量离线回归（`pytest -m 'not docker'`，排除 GBK 1 项） |
| AMZN 57 秒 | `job_flow_succeeded` + `duration_seconds=57` 真实日志 |
| $0.004/次 | `job_cost_usd_total` Prometheus + `deploy/pricing.json` |
| 312 引用链接 | `09_report.pdf` PyMuPDF 统计（222 SEC + 90 web） |
| 30+ 指标 / 8 行看板 | `metrics.py` + `research.json` |
| 10 项指标 | `annual/comparison.json` |

## 待补充（未完成，不写入简历）

- 完整 8 任务 canary 基准（P07-11，需付费授权 + 新 run id）
- 10 家公司效率对照实验（P06-11）
- 故障注入→恢复演示视频（P06-13）
