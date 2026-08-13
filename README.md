# Agent 驱动的自动化投研系统

这是一个面向学习、作品集和工程实践的生产化 Multi-Agent 项目蓝图。用户输入一家美国上市公司的名称或股票代码，系统自动检索公开信息与 SEC 财报，计算关键财务指标，并生成带来源引用的投研报告。

> 重要边界：本项目输出仅用于信息整理和技术演示，不构成投资建议。MVP 只覆盖可在 SEC EDGAR 中识别的美国上市公司。

## 推荐的实现原则

- 先做可运行的纵向切片，再逐步增加工具、数据库、监控和本地容器化部署。
- CrewAI 负责 Agent 任务协作；确定性的 Python 代码负责数值计算、校验、重试和状态持久化。
- 每个工作流步骤都产生结构化输出，并同时写入 PostgreSQL 与工件目录。
- Cline 每次只实现一个小任务，完成测试和学习总结后停止，等待人工确认。
- “成功率 > 95%”和“效率提升 XX%”必须通过基准测试获得，不能先写进简历。

当前项目不依赖 Azure 或其他付费云平台。最终版本使用 Docker Compose 在本地完整运行；Azure 只作为项目完成后的可选升级方向，不属于当前验收范围。

## 文档导航

1. [产品需求文档](docs/01-PRD.md)
2. [技术架构与技术栈](docs/02-ARCHITECTURE.md)
3. [数据库设计](docs/03-DATABASE.md)
4. [工作流、可靠性与评估](docs/04-WORKFLOW-RELIABILITY.md)
5. [细粒度开发路线](docs/05-DEVELOPMENT-ROADMAP.md)
6. [Cline + DeepSeek 提示词手册](docs/06-CLINE-PROMPTS.md)
7. [技术决策所依据的官方资料](docs/07-OFFICIAL-REFERENCES.md)

## 建议的学习式开发方式

1. 在 Cline 的 Plan 模式中使用 `docs/06-CLINE-PROMPTS.md` 的“项目总规划提示词”。
2. 对照 `docs/05-DEVELOPMENT-ROADMAP.md` 选择第一个未完成任务。
3. 切到 Act 模式，运行 `/next-task.md`，只完成该任务。
4. 阅读 Cline 给出的变更说明、测试结果和学习卡片。
5. 自己复述本任务的核心知识，再勾选任务并进入下一项。

## 文档导航（Phase 3.5 补充）

项目在 P03-15 后新增一个补充阶段 **Phase 3.5：受控反思与修订闭环**（位于 P04 之前），
不修改 P03-01~15 编号。它把单一的质量门禁升级为"结构化质量问题 → 受控路由
（发布 / 定向修订 / 补证 / 拒绝）"的闭环，所有循环由 Flow 控制，Agent 之间不互相调用。
详见 `docs/04-WORKFLOW-RELIABILITY.md §2.5`、`docs/05-DEVELOPMENT-ROADMAP.md（Phase 3.5 表格）`。

## Streamlit 轻量操作界面（Phase 4 补充）

项目在 P04-05 后新增 **P04-UI-01~05：Streamlit 轻量操作界面** 系列任务（位于 P0406 前）。
Streamlit 只是 **FastAPI 的一个 HTTP 客户端**，通过后端 API 使用系统，不直接访问
PostgreSQL/Redis，也不直接调用 CrewAI Flow。

- 职责：创建投研任务、轮询任务/步骤状态、查看错误与耗时、查看报告/质量结果/引用
  与数据限制、下载已登记工件。
- 前端规则：只调用 FastAPI；API 地址通过环境变量配置；创建任务使用
  Idempotency-Key；页面不负责业务判断；不在 session state 保存密钥；不显示数据库
  连接字符串和内部文件路径；不引入 React/Vue/Node.js；暂不实现登录、权限和复杂响应式设计。
- 测试：使用 fake HTTP API，不依赖真实数据库、Redis、Docker 或模型。

边界见 `docs/02-ARCHITECTURE.md §11`，实施时序见 `docs/05-DEVELOPMENT-ROADMAP.md`。

## MVP 完成的定义

- 支持按公司名称或 ticker 创建投研任务。
- 解析并保存最新 10-K、最近 10-Q 与 SEC Company Facts 数据。
- 三个 Agent 按固定顺序协作：信息搜集 → 财报分析 → 报告撰写。
- 至少 5 个工具可真实调用，并有单元测试、超时、重试和错误分类。
- 每一步可查看状态和中间工件；失败后可从最近成功步骤恢复。
- 报告中的重要事实和数值具备可追溯引用，派生指标记录公式与输入。
- 完成 100 次受控基准运行后，至少 96 次成功，使工作流成功率严格大于 95%。
