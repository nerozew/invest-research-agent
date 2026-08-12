# 开放问题与待确认假设（Open Questions & Assumptions）

> 本文件由 **P00-01** 产出，汇总在设计文档交叉阅读中发现的问题、已确认口径、后续任务需再决定的假设，以及未来改进建议。
> 本文件只记录问题与分析，不包含任何代码实现。

## 1. 阅读基线

- 本任务审阅范围：`README.md` + `docs/01-PRD.md` 至 `docs/07-OFFICIAL-REFERENCES.md`，共 **8 个文件**。
- 路线图 P00-01 标题中的“7 份文档”指 `docs/` 目录中的 7 份文件（01–07）。
- 状态分类说明：

| 状态 | 含义 |
|---|---|
| `已确认` | 人工确认过的口径，后续按此执行，不再视为冲突 |
| `当前阻塞` | 会阻塞当前或最近任务，需立即解决 |
| `后续任务再决定` | 在特定 Phase/任务执行前再决定 |
| `风险提醒` | 已知风险，需要留意但当前不阻塞 |
| `未来改进` | 当前不处理，留待项目后期优化 |

---

## 2. 已确认口径

### 2.1 成功率阈值（原 B1）

- 状态：`已确认`
- 证据：
  - `README.md` 第 43 行：“完成 100 次受控基准运行后，至少 96 次成功，使工作流成功率严格大于 95%”
  - `docs/01-PRD.md` §9.1（118 行）：“有效任务成功率：100 次受控运行中至少 96 次达到‘成功’定义，即严格大于 95%”
  - `docs/04-WORKFLOW-RELIABILITY.md` §10（194 行）：“只有总体成功次数至少 96/100 且硬质量门禁无绕过，才可在简历写‘任务成功率 >95%’”
- 结论：**固定运行 100 次，至少成功 96 次（96/100）**，才可宣称“成功率 >95%”。三份文档口径一致，记录为已确认。

### 2.2 Agent 工具计数口径（原 B5）

- 状态：`已确认`
- 证据：
  - `docs/02-ARCHITECTURE.md` §4（48 行）：“工具设计（至少 5 个，实际规划 9 个）”
  - `README.md` 第 40 行：“至少 5 个工具可真实调用”
- 结论：**“至少 5 个工具”指暴露给 Agent、具有明确 Pydantic 输入输出契约的顶层 Agent Tool**。当前架构规划 9 个：`CompanyResolverTool`、`GoogleSearchTool`、`SECSubmissionsTool`、`SECCompanyFactsTool`、`FilingDownloaderTool`、`DocumentParserTool`、`FinancialCalculatorTool`、`ArtifactStoreTool`、`CitationVerifierTool`。
- HTML parser、PDF parser 等内部 adapter 只由 `DocumentParserTool` 内部调用，**不单独计算为 Agent Tool**。

### 2.3 报告语言传递链（原 B6）

- 状态：`后续任务再决定`（设计已确认，实现推迟到 Phase 3）
- 证据：
  - `docs/01-PRD.md` 第 9 行：“报告语言：中文为主，可配置英文”
  - `docs/03-DATABASE.md` 81 行：`language text NOT NULL DEFAULT 'zh-CN'`
- 结论（已确认的传递链）：
  `ResearchRequest.language` → CrewAI Flow state → Agent Task 输入 → `ReportDraft` → Jinja2 报告模板。
- MVP 默认 `zh-CN`，允许配置英文。该设计在 Phase 3（自 P03-02 提示词起）实现，不阻塞 P00-01。

---

## 3. 当前阻塞

- **无。** P00-01 本身是纯文档分析任务，不依赖任何第三方工具或运行环境。

---

## 4. 后续任务再决定

### 4.1 uv 工具链（原 B4）

- 状态：`后续任务再决定`（P00-03 执行时若不可用则为阻塞）
- 证据：
  - `docs/05-DEVELOPMENT-ROADMAP.md` P00-03：“用 `uv` 建立 Python 3.12 项目”
  - 本机环境：当前工作区已检测到 `pip`；`uv` 是否可用**未确认**。
- 决策：
  - P00-02 只做 Git 初始化，涉及不到 uv。
  - 执行 P00-03 时，**优先安装并使用 uv**；不得擅自降级为 `pip + venv`。
  - 若 P00-03 执行时无法安装 uv，**停止并报告用户**，由用户决定后续方案。
- 本轮（P00-01）不得安装 uv。

### 4.2 `DocumentManifest` 未列入架构关键数据对象（新增发现）

- 状态：`后续任务再决定`（Phase 2 文档处理相关任务前确认）
- 证据：
  - `docs/04-WORKFLOW-RELIABILITY.md` §2 步骤表第 03 步输出 schema 为 `DocumentManifest`
  - `docs/02-ARCHITECTURE.md` §7“关键数据对象”列出 10 个对象（ResearchRequest、CompanyIdentity、ResearchPack、FinancialFact、MetricResult、FinancialAnalysisPack、ReportDraft、QualityReport、RunManifest），**未包含 `DocumentManifest`**
- 说明：步骤 03“文档处理”的输出 schema 没有出现在 02 架构 §7 的关键数据对象清单中。实现文档下载/解析相关任务（P02-08 起）前，需确认该模型的定义位置。建议归入 `domain`，与 ResearchPack 同级。

### 4.3 步骤幂等键与表唯一约束的张力（新增发现）

- 状态：`后续任务再决定`（P01-06 / P01-12 / P01-13 实现时定机制）
- 证据：
  - `docs/04-WORKFLOW-RELIABILITY.md` §6（125 行）：“步骤幂等键为 `job_id + step_name + input_hash + schema_version`”
  - `docs/03-DATABASE.md` 117 行：`workflow_steps` 表 `UNIQUE (job_id, step_name)`
  - `docs/04-WORKFLOW-RELIABILITY.md` §6（129 行）：“提示词、模型、公式或 schema 版本变化后，input hash 改变，相关下游步骤必须失效重算”
- 问题：幂等键包含 `input_hash` 和 `schema_version`，但表唯一约束只有 `(job_id, step_name)`。当上游变化导致同一步骤以新 input_hash 重算时，表约束会阻止插入新记录。需要明确“失效重算”是**原地更新原行**，还是**允许同一 (job_id, step_name) 存在多版本行**（并相应调整唯一约束）。

### 4.4 PRD 指标清单与路线分组的边界（新增发现）

- 状态：`后续任务再决定`（P02-15 / P02-16 实现前确认）
- 证据：
  - `docs/01-PRD.md` §8（99–111 行）列出 **10 个指标**：收入增长率、毛利率、营业利润率、净利率、流动比率、资产负债率、经营现金流率、自由现金流、ROA、ROE
  - `docs/05-DEVELOPMENT-ROADMAP.md` P02-15：“5 个利润与增长指标”；P02-16：“5 个资产负债/现金流指标”
- 说明：总数一致（10 = 5 + 5），但 PRD 未规定每个指标归属“利润与增长”还是“资产负债/现金流”分组。ROA / ROE 的分组归属不明确，建议实现时统一口径（例如 ROA/ROE 归入“利润与增长”，因为以净利润为分子）。

---

## 5. 风险提醒

### 5.1 Phase 5 监控栈与学习进度（原 B7）

- 状态：`风险提醒`
- 证据：`docs/05-DEVELOPMENT-ROADMAP.md` Phase 5（P05-05 ~ P05-08）引入 structlog、Prometheus、Grafana、OpenTelemetry
- 风险：用户第 5 周“监控与部署”尚未系统学习。Phase 5 为后续阶段，届时**边开发边学**，不要求现在提前掌握。
- 建议：进入 Phase 5 前先安排第 5 周学习，或把 P05-05 ~ P05-08 拆成更小的学习单元，避免一次引入过多新概念。

---

## 6. 未来改进

### 6.1 ADR 管理方式（原 B2）

- 状态：`未来改进`
- 证据：
  - `docs/02-ARCHITECTURE.md` §10“架构决策记录（ADR 摘要）”：ADR-001 ~ ADR-005
  - `.clinerules/02-engineering.md` 16 行：“不得在没有批准 ADR 的情况下替换已经确定的技术方案”
- 结论（已确认）：ADR-001 至 ADR-005 以摘要形式保存在 `docs/02-ARCHITECTURE.md` §10，**当前即为有效的架构决策记录**；`docs/adr/` 目录暂时不创建，不阻塞 P00-01 或任何后续基础任务。
- 未来：若需要修改已确定的架构，再单独创建 `docs/adr/` 并为变更增加正式 ADR 文件。

### 6.2 `.clinerules` 文档范围引用待更新（原 B3）

- 状态：`未来改进`（审阅范围已确认；规则措辞需要更新）
- 证据：
  - `.clinerules/01-project.md` 第 3 行：“必须阅读 `README.md` 以及 `docs/01` 到 `docs/05` 的设计文档”
  - 实际 `docs/` 目录包含 01–07 共 7 份文档
- 说明：P00-01 已确认审阅范围为 README + docs/01–07 共 8 个文件。`.clinerules/01-project.md` 引用的范围（01–05）与实际文档数量（01–07）不一致。建议未来更新规则措辞，但不属于本任务范围。

---

## 7. 术语说明

- 本项目当前采用“**文档驱动的开发流程**”（先设计后实现）。
- **不使用** “DDD” 缩写描述该流程，避免与 Domain-Driven Design（**领域驱动设计**）混淆。

---

## 8. 交叉核验总结

| 核验对 | 结果 |
|---|---|
| PRD §3.2/README vs 架构 Agent 数量 | 一致（3 个 Agent：信息搜集 → 财报分析 → 报告撰写） |
| 架构 §4 工具数量 vs README“≥5” | 一致（9 个顶层 Agent Tool，见 2.2） |
| 架构 §7 数据对象 vs 04 步骤输出 | 1 处遗漏：`DocumentManifest`（见 4.2） |
| 03 数据库状态枚举 vs 04 状态机 | 一致（step 级与 job 级状态分离清晰） |
| PRD §8 指标清单 vs 05 P02-15/16 | 数量一致（10 个），分组边界待定（见 4.4） |
| 04 §6 幂等键 vs 03 唯一约束 | 存在张力，待定机制（见 4.3） |
| 04 测试金字塔 vs 05 各 Phase | 一致（单元/契约/集成/录制/E2E/基准/故障注入均有对应任务） |
| README / PRD §9.1 / 04 §10 成功率 | 一致（96/100 = >95%，已确认，见 2.1） |