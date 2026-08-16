# Phase 6 交接文档（Context Handoff）

> 用途：当 Cline 上下文接近上限或阶段性收口时，把已完成工作、下一任务、测试结果、
> Git 状态完整记录下来，供新窗口/新会话使用短提示词无缝继续。本文件由 Cline 自动维护。

## 1. 当前状态总览

- **分支**：`agent/m2-deterministic-tools`（本地 commit，**未 push**）
- **Phase 6 状态**：P06-01~05 ✅；新增 P06-05A（SEC Company Facts 真实数据流）✅；P06-06~14 未开始。
- **P06-02**：PDF 重构为 markdown-it-py + PyMuPDF Story，4 页样例已逐页视觉检查通过，已标 ✅。
- **P06-05**：已补上原先缺失的 Outbox/Celery W3C trace-context 传播，不再只是进程内模拟。
- **当前验证**：全量 764 passed / 19 skipped；Ruff 全绿；`mypy src` 108 个源文件全绿。

## 2. 已完成任务与本地 commit

| 任务 | 内容 | 测试 | 本地 commit |
|---|---|---|---|
| 测试基建 | 沙箱安全 tmp 目录（conftest 覆盖 mode=0o700 问题）+ CrewAI SQLite 存储重定向 + connectivity_check ruff 修复 | 全量回归 692 passed | `1803251` |
| P06-01 ✅ | Jinja2 固化 Markdown 报告模板 + golden tests（reporting/renderer.py + report.md.j2） | test_report_renderer 15 passed；全量 708 | `d01a92c`（代码）、`533437e`（docs） |
| P06-02 ✅ | CommonMark→HTML→PyMuPDF Story；粗体/表格/列表/代码/链接/中文分页；去重 H1；4 页视觉检查 | 相关 28 passed | 本轮待提交 |
| P06-03 ✅ | 工件生命周期清理（临时文件/过期任务/.keep 保护/基准保留 N 个；dry_run 默认） | test_artifact_lifecycle 13 passed；全量 731 | `dd2aec7`（代码）、`cf80d9b`（docs） |
| P06-04 ✅ | 生产配置档位：environment=production 占位符密钥 fail-fast + 密钥卫生测试 | test_secrets_config 11 passed；全量 742 | `bc12798`（代码）、`6403b05`（docs） |
| P06-05 ✅ | trace carrier 同 Outbox 持久化，Celery headers 投递，Worker 提取 parent context | trace/outbox/dispatcher 18 passed | 本轮待提交 |
| P06-05A ✅ | SEC Company Facts 并行预取、filed/as_of 截断、concept mapping 选数、Analysis 硬约束注入 | 相关 42 passed | 本轮待提交 |

## 3. 关键产物文件（P06 增量）

```
src/invest_research/reporting/renderer.py          # P06-01 Jinja2 渲染器 + build_render_input
src/invest_research/reporting/templates/report.md.j2  # 报告模板（封面/正文/来源链接/限制/免责声明）
src/invest_research/reporting/pdf.py                # P06-02 MarkdownPdfRenderer + render_page_png
tests/fixtures/golden_report.md                     # P06-01 golden 基准（逐字节比对）
docs/p06-02-samples/                                # 示例 PDF(3页,42链接)/MD/首页+次页 PNG（真实 AAPL live 工件渲染）
scripts/render_report_pdf.py                        # 示例重新生成脚本
src/invest_research/infrastructure/artifact_lifecycle.py  # P06-03 CleanupPolicy/Stats/Service
src/invest_research/infrastructure/observability/tracing.py # P06-05 span()/OTLP/可重复配置
deploy/otel-collector.yaml                          # P06-05 本地 collector（.gitignore 已放行 /deploy/）
tests/test_report_renderer.py / test_pdf_renderer.py / test_artifact_lifecycle.py / test_secrets_config.py / test_tracing_local.py
```

## 4. 已更新文档

- `docs/05-DEVELOPMENT-ROADMAP.md`：P06-01 ✅ / **P06-02 ⏳（视觉确认待人工，未标 ✅）** / P06-03 ✅ / P06-04 ✅ / P06-05 ✅。
- `docs/09-LEARNING-LOG.md`：P06-01~05 五条学习条目（含检查问题，未附答案）。
- `docs/10-RUN-GUIDE.md`：新增 §7 生产配置与密钥管理（profile / 卫生红线 / 部署前检查清单）。
- `.env.example`：ENVIRONMENT=production 说明、密钥红线注释、OTEL 变量占位。

## 5. 等待人工确认的事项（已清空）

> P06-02 的 4 页 PDF 已逐页视觉检查并修复，下方两项是历史记录，不再是待办。
> 下一个需用户配合的节点是 P06-06：恢复 WMI/WSL2 并安装 Docker Desktop。

1. **P06-02 视觉检查（历史记录，已完成）**：新样例为 4 页，全部页已检查。原检查项为：
   `sample_report_page1.png` / `page2.png`，确认：① 中文字体渲染正常（无豆腐块/乱码）；
   ② 来源链接可点击（第 2~3 页约 42 个 SEC 链接）；③ 分页断行美观。确认后把路线图
   `P06-02 ⏳` 改为 `P06-02 ✅` 并提交（若发现问题，修 `reporting/pdf.py` 后重跑测试与示例）。
2. **P06-02 已知外观问题**：正文若自带 `# 标题` 会与模板封面标题重复（LLM 初稿内容）；如需去重可让
   `build_render_input` 剥离初稿首个匹配的 H1。

## 6. 已知风险与注意事项

1. **本会话测试运行方式（沙箱特定）**：受限沙箱下 `uv` 缓存不可写（需 `UV_CACHE_DIR` 指到工作区内）、
   pytest 默认 tmp 目录（mode=0o700）会被拒写。已通过 `tests/conftest.py` 覆盖 `tmp_path_factory`
   解决（目录默认权限 + 会话唯一根 + 不删除），并重定向 `appdirs.user_data_dir` 让 CrewAI SQLite
   存储走临时目录。日常 `uv run pytest` 在正常环境仍可用；沙箱下用
   `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`。
2. **OTel `set_tracer_provider` Once 守卫**：已重置守卫使 `setup_tracing` 可重复配置（最后一次生效）；
   这是测试与进程内重配的必要行为，勿回退。
3. **`deploy/` 目录刚加入 .gitignore 白名单**（`!/deploy/`）；后续 P06-06 compose 若新增根目录文件需同步放行。
4. **未 push、未触发 Actions、未部署、未 SSH**：全部按夜间安全规则执行；下一次任何 push 前先确认。
5. **Windows WMI 环境故障**：本机 `platform.system()/machine()` 会卡在 WMI，连带
   SQLAlchemy/Prometheus/Celery 导入假死。本轮测试仅在 pytest 启动器中代替
   这两个纯系统信息查询；安装 Docker 前应先恢复 WMI/PowerShell/WSL 健康。

## 7. Git 状态（本快照）

本轮代码 commit：

- `5c9e2df` P06-02 PDF 样式渲染与逐页验收；
- `33f0643` P06-05 Outbox/Celery trace context 跨进程传播；
- `4a0b1a9` P06-05A point-in-time SEC facts 注入 Analysis；
- `48514bc` 修复旧 DB fault-injection fake 与显式 `flush()` 不一致。

最终验证：`764 passed / 19 skipped / 1 warning`；Ruff `All checks passed`；
`mypy src` 108 个源文件全绿。本轮未 push、未调真实付费 API、未 SSH、未部署。

## 8. 新窗口继续短提示词

> 【Phase 6 继续】读取 `docs/12-PHASE6-HANDOFF.md`、`docs/13-ERRORS-REVIEW.md`
> 和 `docs/05-DEVELOPMENT-ROADMAP.md`。P06-01~05A 已完成，全量 764 passed / 19 skipped，
> Ruff/mypy 全绿。下一步是 P06-06，但当前 Windows 无 Docker，WMI 查询卡死，
> 需先恢复 WMI/PowerShell/WSL2 并安装 Docker Desktop。Docker 可用后再完成 compose
> profile 的真实启动验收；未经授权不 push、不 live API、不 SSH。
