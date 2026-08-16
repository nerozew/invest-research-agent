# Phase 6 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限或阶段性收口时，把已完成工作、下一任务、测试结果、
> Git 状态完整记录下来，供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`（本地 commit，**未 push**）
- **Phase 6 状态**：P06-01~05 ✅；P06-05A ✅；P06-06 ✅；**P06-06A ✅；P06-06B ✅；P06-06C ✅**；P06-07~14 未开始。
- **P06-06**：完整本地 Docker Compose observability profile（Prometheus / Grafana / OTel Collector / Jaeger）已真实启动并通过健康检查。
- **P06-06A**：每任务 fast/deep 研究档位（前端单选 + 0007 迁移 + Worker 路由 + fast 关闭思考模式）。
- **P06-06B**：ProgressSink 实时步骤状态（幂等创建 00-07、合法状态转换、失败收口 running、终态清空 current_step、前端阶段中文映射）。
- **当前验证**：相关测试 87 passed（含新增 SqlProgressSink/前端/ExecutionRecorder 不重复插入）；Ruff 全绿；`mypy src` 110 个源文件全绿；0007 迁移真实 PostgreSQL upgrade→downgrade→upgrade 通过；fast/deep fake smoke 通过（running 期间 current_step+steps、终态 8 步全成功、无真实 API 调用）。
- **当前 commit**：P06-06A 代码 `b2d773e`（已存在）+ 本轮新增（未提交）。

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| P06-01 ✅ | Jinja2 固化 Markdown 报告模板 + golden tests（reporting/renderer.py + report.md.j2） | test_report_renderer 15 passed；全量 708 | `d01a92c`（代码）、`533437e`（docs） |
| P06-02 ✅ | CommonMark→HTML→PyMuPDF Story（4 页样例已逐页视觉检查） | 相关 28 passed | `5c9e2df` |
| P06-03 ✅ | 工件生命周期清理（临时文件/过期任务/.keep 保护/基准保留 N 个；dry_run 默认） | test_artifact_lifecycle 13 passed | `dd2aec7`（代码）、`cf80d9b`（docs） |
| P06-04 ✅ | 生产配置档位：environment=production 占位符密钥 fail-fast + 密钥卫生测试 | test_secrets_config 11 passed | `bc12798`（代码）、`6403b05`（docs） |
| P06-05 ✅ | trace carrier 同 Outbox 持久化，Celery headers 投递，Worker 提取 parent context | trace/outbox/dispatcher 18 passed | `33f0643` |
| P06-05A ✅ | SEC Company Facts 并行预取、filed/as_of 截断、concept mapping 选数、Analysis 硬约束注入 | 相关 42 passed | `4a0b1a9` |
| P06-06 ✅ | 完整本地 Docker Compose observability profile（Prometheus/Grafana/OTel Collector/Jaeger） | test_compose_observability 12 passed；相关 36 passed | 本轮待提交 |
| P06-06A ✅ | 每任务 fast/deep 档位：ResearchProfileMode + 0007 迁移 + 前端单选/徽章 + Worker 路由 | 相关 87 passed（含新增前端/进度）；Ruff/mypy 全绿；0007 真实 PostgreSQL upgrade→downgrade→upgrade 通过 | 代码已在 `b2d773e`（后端）+ 本轮前端 |
| P06-06B ✅ | 实时步骤状态：ProgressSink + SqlProgressSink 短事务/幂等创建 + Flow/Worker 步骤标记 + 前端阶段中文映射 | SqlProgressSink 11、ExecutionRecorder 不重复、前端 19、Worker 87 全绿 | 本轮待提交 |
| P06-06C ✅ | 接通 Prometheus 业务指标与 Jaeger 真实 trace 数据链：OTLP 端点规范化 + 业务指标事件 + Worker 多进程指标端点 + Prometheus/Grafana 修复 | 离线 48+48 passed；Ruff/mypy 全绿；Docker fake 验收：两 target up、metrics/Histogram 非空、Jaeger trace 跨 Outbox/Celery 关联、无 OTLP 404、无真实外部调用 | 本轮待提交 |

## 3. 关键产物文件（P06 增量）

```
compose.yml                                    # P06-06：observability profile（prometheus/grafana/otel-collector/jaeger）
deploy/prometheus/prometheus.yml               # P06-06：采集 API /metrics
deploy/grafana/provisioning/datasources/prometheus.yml  # P06-06：自动 provision Prometheus datasource（uid=prometheus）
deploy/grafana/provisioning/dashboards/dashboard.yml    # P06-06：自动加载 research.json dashboard
deploy/otel-collector.yaml                     # P06-05/06：OTLP HTTP → debug + Jaeger，health_check extension
tests/test_compose_observability.py            # P06-06：12 个静态校验测试
src/invest_research/reporting/renderer.py      # P06-01 Jinja2 渲染器 + build_render_input
src/invest_research/reporting/templates/report.md.j2  # 报告模板（封面/正文/来源链接/限制/免责声明）
src/invest_research/infrastructure/observability/tracing.py  # P06-05 span()/OTLP/可重复配置
tests/fixtures/golden_report.md                # P06-01 golden 基准（逐字节比对）
docs/p06-02-samples/                           # 示例 PDF(4页,42链接)/MD/PNG（真实 AAPL live 工件渲染）
scripts/render_report_pdf.py                   # 示例重新生成脚本
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P06-06 ✅ / P06-07 为下一候选。
- `docs/09-LEARNING-LOG.md`：P06-06 学习条目（3 知识点 + 检查问题 + 已知限制）。
- `docs/10-RUN-GUIDE.md`：此前已含 §7 生产配置与密钥管理。

## 5. 等待人工确认的事项（已清空）

> P06-06 的 observability 服务已全部健康；P06-07 是下一任务（本地部署 smoke test），
> 需用户授权切换到该任务后再执行。

## 6. 已知风险与注意事项

1. **观测镜像体积大、慢网络下首次 pull 超时**：Prometheus v2.55.1 / Grafana 11.3.0 /
   Jaeger 1.62.0 均较大；网络不稳定时 `docker pull` 会 10 分钟超时中断。
   解法：串行重试 `docker pull`（Docker 会断点续传已下载 layer，复用不重复下载）。
2. **OTel Collector 官方镜像是 distroless（无 shell）**：compose healthcheck 用
   `["CMD", "/otelcol", "--version"]` 探测进程存活；真实健康由配置中的
   `health_check` extension（13133 端口）提供。
3. **Jaeger 使用 all-in-one 内存存储**：重启丢失链路数据，仅用于本地查看，不用于生产。
4. **`deploy/` 目录已在 .gitignore 白名单放行**（`!/deploy/`）；新增根目录文件需同步放行。
5. **未 push、未触发 Actions、未部署、未 SSH**：全部按夜间安全规则执行。
6. **Windows WMI 环境故障（历史）**：本机 `platform.system()/machine()` 曾在 WMI 卡死；
   本轮测试与 Docker Desktop 均正常，未再复现。

## 7. Git 状态（本快照）

本轮代码 commit（均为本地，**未 push**）：

- `21b3133` feat(p06-06a): add per-job research profile to frontend（前端档位单选 + 徽章）
- `f7034e6` feat(p06-06b): real-time step progress with SqlProgressSink and minimal frontend stage display（ProgressSink + 步骤标记 + 前端阶段映射 + 测试 + 文档）
- P06-06B 收口新增本地 commit（未 push）：`fix(p06-06b): enforce real-time progress invariants and cancellation cleanup`

### P06-06B 收口要点（2026-08-16）

- **状态不变量**：唯一 running（SQL NOT EXISTS）、current_step 只指向 running、终态清空、attempt_count 原子 +1、00 在 01 前 succeeded；
- **取消收口**：DELETE 经 CancelStepCleanup 端口（SqlProgressSink.cancel_pending_steps）把 running/pending→skipped、清空 current_step、不删历史；
- **孤儿任务已收口（未删行/工件）**：e38d85f0/73ec29cf → cancelled、current_step=null、无 running、pending 已 skipped；
- **fake smoke（fast+deep）**：唯一 running、current_step 与 running 一致、8 步顺序正确、attempt≥1、终态 null、fast/deep 正确、日志无 SEC/Serper/真实模型调用；
- **模型配置（仅复核）**：容器已读取 .env：Research/Analysis=qwen3.6-flash、Writer=qwen3.5-plus、thinking=false；建议 Research 快速模型（thinking=false）、Analysis 更强非思考模型（额度不足用 Flash）、Writer 写作质量模型（thinking=false）；Analysis 是否开 thinking 需后续固定评测决定；
- **前端增强**：中国时区（Asia/Shanghai）简化格式 + 列表/详情创建/开始/结束时间与总耗时。

最终验证：相关测试 87 passed；Ruff `All checks passed`；`mypy src` 110 个源文件全绿；
0007 迁移真实 PostgreSQL upgrade→downgrade→upgrade 通过；fast/deep fake smoke 通过
（running 期间 current_step+steps、终态 8 步全成功、无真实 API 调用）。
本轮未 push、未调真实付费 API（fake smoke 已覆盖）、未 SSH、未部署。

## 8. 新窗口继续短提示词

> 【Phase 6 继续】读取 `docs/12-PHASE6-HANDOFF.md`、`docs/13-ERRORS-REVIEW.md`
> 和 `docs/05-DEVELOPMENT-ROADMAP.md`。P06-01~06 已完成，相关测试 36 passed、
> Ruff/mypy 全绿；完整 Docker Compose observability profile 已真实启动（10 服务健康）。
> 下一步是 P06-07（本地部署 smoke test：health、任务执行、报告下载、指标和链路查询），
> 需用户授权切到该任务；未经授权不 push、不 live API、不 SSH。