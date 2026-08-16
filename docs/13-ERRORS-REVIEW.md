# 错误回顾（Errors Review）

> 记录 Phase 5.5 部署验收期间（Ubuntu live 环境 + 本地开发）遇到的全部真实错误：现象 → 根因 → 修复 → 验证 → 面试要点。
> 按发生时间排序；每条都包含【一句话根因】，面试时先讲根因再讲修复。

---

## 1. worker 启动即崩溃：httpx 拒绝非 ASCII User-Agent

- **现象**：Ubuntu 容器里 worker 一启动就报 ascii codec can't encode character 异常（字符 \u7531，即中文“由”），进程直接退出。
- **根因**：默认 User-Agent 字符串里含中文“由”字；httpx 要求 HTTP 头值必须 ASCII，非 ASCII 字符编码直接失败。数据库/日志能存中文，但协议头不行——协议层与业务层的字符集边界不同。
- **修复**：
  - 默认 http_user_agent 改为纯 ASCII：invest-research/0.1 (+your-email@example.com)；
  - compose.yml 增加 HTTP_USER_AGENT 环境变量转发，部署时可覆盖。
- **验证**：worker 在容器内正常启动；本地回归测试覆盖配置默认值。
- **面试要点**：第三方库的异常信息可能完全看不出是“配置默认值”的问题，要顺着编码栈回查配置；HTTP 头是 ASCII 协议，业务文本是 UTF-8 世界。

## 2. 建任务 500：SQLAlchemy UOW 刷新顺序导致 PostgreSQL 外键违反

- **现象**：API 创建任务返回 500，日志里却误报“job 已存在”；真实错误是 outbox_events_job_id_fkey 外键违反。
- **根因**：SQLAlchemy Unit of Work 在同一事务里按插入顺序刷新，实际顺序是 outbox_events → research_jobs——outbox 行先落库时父行 research_jobs 还不存在 → PostgreSQL 严格外键直接报错。SQLite 默认 PRAGMA foreign_keys=OFF 不检查外键，本地测试全绿、生产一跑就炸——测试环境掩盖了数据库语义差异。
- **修复**：
  - session.flush() 先把 job 落库，再插入 outbox 行（commit 97d4033）；
  - 回归测试显式 PRAGMA foreign_keys=ON，让 SQLite 也检查外键。
- **验证**：建任务接口在 PostgreSQL 上 200；服务器应急 DROP CONSTRAINT 后，新镜像重新 ADD CONSTRAINT（ON DELETE CASCADE）恢复成功。
- **面试要点**：① 外键/约束/事务类测试必须跑真实 PostgreSQL（Testcontainers/本地 Docker），不要用 SQLite 掩盖差异；② UOW 刷新顺序是真实生产事故源，flush() 是显式控制手段；③ 对 IntegrityError 的误判（把 FK 违反当成“已存在”）源于没看 exc.orig。

## 3. Live 任务极慢/超时：LLM 默认开启思考模式

- **现象**：千问模型请求 Request timed out，research 步骤延迟巨大。
- **根因**：Qwen3.5 默认进入思考（reasoning）模式，输出长、延迟高，对工具调用型 agent 是纯开销。
- **修复**：
  - LLM_ENABLE_THINKING=false 环境变量 + 请求 extra_body.enable_thinking=false 透传（additional_params 通道）；
  - fast 档配置强制关闭思考，deep 档由环境变量控制。
- **验证**：live 任务各步骤耗时显著下降（research 39s 级）。
- **面试要点**：模型“默认行为”可能是性能瓶颈，先量化各步骤耗时再定位；不要用“加大超时”掩盖问题——曾临时把超时 60s 提到 120s/300s，被要求回退：超时掩盖的是延迟根因而不是修复它。

## 4. SEC EDGAR 404：submissions URL 缺 CIK 前缀

- **现象**：SEC submissions 请求 404，拿不到公司申报数据。
- **根因**：EDGAR API 要求 https://data.sec.gov/submissions/CIK##########.json（CIK 前缀 + 10 位补零），实现漏了 CIK 前缀。
- **修复**：build_submissions_url 补 CIK{cik}.json 格式。
- **验证**：本地录制脚本一直是对的（工具+测试都错），修正后与录制对齐，请求 200。
- **面试要点**：外部 API 的 URL 形态是契约；先用真实请求（录制脚本）校准再写实现，两套实现不一致时以真实请求为准。

## 5. 财务事实校验过严：value gt=0 拒绝负值/零值

- **现象**：正常公司（亏损/无收入年份）的财务事实被 Pydantic 校验拒绝。
- **根因**：FinancialFact.value 写了 gt=0——财务数值本来就可能是负（亏损）或零，把业务语义误当成脏数据。
- **修复**：去掉 gt=0，只保留 Decimal 类型约束。
- **面试要点**：领域模型的校验约束必须来自领域语义；金融领域负值不是异常。

## 6. 财报日期门禁过严：period_end 必须 <= as_of_date

- **现象**：质量检查把“财年结束日 > 数据截止日”的合法报告误判为 CRITICAL 违规。
- **根因**：财年结束日（如 2026-06-30）≠ 数据截止日（as_of 2026-07-31），原始门禁把合法数据判死。
- **修复**：仅当 period_end > as_of_date 时才判 period_end_mismatch（CRITICAL），消息改为“period_end 晚于 as_of_date（禁止使用截止日之后的数据）”。
- **面试要点**：质量门禁的边界条件要用真实业务场景校准，过严与过松同样有害。

## 7. 验收项缺失：evidence.invocation_summary 为空

- **现象**：验收要求工具调用证据，prefetch（预取）阶段的调用没被记录，证据缺项。
- **根因**：并行预取路径与工具执行路径是两条代码路径，预取调用没写入 stats 计数。
- **修复**：resolve_and_prefetch 把 SEC/web 调用计入 stats；evidence 构建在 stats 为空时从 PerformanceRecorder 兜底。
- **面试要点**：验收证据要覆盖所有外部调用路径（包括缓存/预取旁路），少统计会像没调用一样被验收卡住。

## 8. 上下文爆炸：Maximum iterations reached + ~30 万 token

- **现象**：live 任务中途报 Maximum iterations reached，上下文达 30 万 token 量级。
- **根因**：工具结果太大（facts 200 条、search 20 条、snippet 200 字符），agent 工具循环每轮都把这些内容粘回上下文——上下文复利膨胀；research_max_iter 太小（5）导致没干完就被截断、重试再叠加膨胀。
- **修复**：
  - 结果瘦身：facts 15 条 / search 10 条 / snippet 60 字符；
  - research_max_iter 5 → 8（给足轮次，避免“截断→重试→更膨胀”恶性循环）；
  - research 任务移除 output_pydantic（CrewAI 在 kickoff 内校验失败会抛错；改由 runner 做有界 JSON 提取兜底）。
- **验证**：live 任务全程 token 可控，research 39s 完成。
- **面试要点**：① agent 上下文成本随工具循环轮数复利增长，结果大小与轮次上限要联动控制；② max_iter 太小引发重试螺旋；③ 框架内 schema 校验失败是隐性故障源，校验放框架外可控性更强。

## 9. Analysis 空 facts 撞 schema → 任务永久卡 running（本轮核心修复）

- **现象**：live 任务 Analysis 输出空 facts，报 FinancialAnalysisPack.facts: List should have at least 1 item，任务状态永远停在 running（无终态、无失败记录）。
- **根因**（两层）：
  1. 数据流缺口：ResearchPack 只带 sources 不带 facts，Analysis 工具白名单无 SEC 工具 → live 下 Analysis 拿不到真实 XBRL 事实，只能靠 LLM 记忆，空输出是真实边界情况；
  2. 健壮性缺口：schema 要求 facts 至少 1 条，CrewAI 在 kickoff 内部校验失败直接抛错，而执行服务没有“异常 → 失败终态”的兜底。
- **修复**：
  - FinancialAnalysisPack.facts 改 default_factory=list 允许空（报告据实标注“无财务事实”而非崩溃）；
  - ExecutionStatusWriter 协议增加 mark_failed；ExecuteResearchJobService.process 用 try/except 包 flow_runner.run，异常 → mark_failed（running→failed + completed_at）→ re-raise 让 worker 记日志；
  - worker _RepoWriter.mark_failed 条件更新实现。
- **验证**：新增 test_financial_analysis_pack_allows_empty_facts、test_flow_failure_marks_failed_and_reraises；回归 71 passed，ruff/mypy 干净（commit b06efb0）。
- **面试要点**：① 状态机必须有“异常 → 终态”兜底，任何异常都不能让任务悬在运行态；② schema 校验失败要区分“数据真错”与“合法边界情况”——空结果是合法业务输出时 schema 就要允许；③ 修复分两层：先止血（不崩溃、状态正确），再补数据流。

## 10. 服务器应急：临时删除外键约束（运维手段）

- **现象**：生产 PG 被 #2 的 FK 违反卡住建任务，等新镜像太慢。
- **处理**：服务器临时 DROP CONSTRAINT 解封；代码修复后新镜像重新 ADD CONSTRAINT（ON DELETE CASCADE）恢复成功。
- **面试要点**：应急手段必须有恢复计划——删约束是止血，恢复约束必须排进发布清单并验证。

## 11. GitHub 443 连接重置：push 反复失败

- **现象**：git push 报 Recv failure: Connection was reset，本机与用户机器间歇失败；ghcr.io 一直可达。
- **处理**：等待网络恢复 + 重试（曾多次失败后最终推送成功；最新 b06efb0 已提交本地，推送待网络恢复重试）。
- **面试要点**：区分“代码问题”与“网络问题”——本地全绿 + 错误在连接层，按网络故障处理：重试而不是改代码。

## 12. 开发工具链：中文路径文件写入偶发 ReplaceFileW EIO (Win32 1175)

- **现象**：对含中文路径的已存在文件 write/edit 偶发 EIO（Windows 文件替换失败）。
- **处理**：重试；或“read 全量 → write 全量重写”绕开；长文件注意单次读取行数上限（分段读）。
- **面试要点**：工具链故障用工程手段绕开（幂等重试 + 全量重写），不影响业务改动本身。

## 13. 沙箱环境：uv 缓存不可写、pytest 缓存

- **现象**：受限环境下 uv 默认缓存目录不可写；pytest 的 .pytest_cache 在部分沙箱场景报错。
- **处理**：UV_CACHE_DIR 指向工作区；沙箱下测试用 .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider。
- **面试要点**：环境差异（缓存目录权限）是部署/CI 常见坑，配置项要可覆盖（环境变量）。

## 14. P06-05 假阳性：进程内 span 嵌套不等于跨进程 trace

- **现象**：路线图宣称 FastAPI→Worker→Flow→Tool 可关联，但测试只在一个 Python 进程中嵌套四个 span。
- **根因**：API 与 Worker 在不同进程，Celery/Redis 会切断内存上下文；没有序列化 W3C `traceparent` 就会生成两条独立 trace。
- **修复**：创建 Job 时把 trace carrier 与 outbox 事件同事务持久化；relay 传入 Celery headers；Worker 提取 parent context 后创建 `worker.process`。
- **验证**：离线契约测试验证 carrier 序列化/反序列化后 trace_id 与 parent.span_id 不变；真实 collector 查询留待 P06-07。
- **面试要点**：分布式链路的核心不是“每层都有 span”，而是跨网络/消息边界传递同一 trace context。

## 15. 空 facts 只是止血：Analysis 根本没拿到 SEC XBRL

- **现象**：允许 `FinancialAnalysisPack.facts=[]` 后任务不再崩溃，但报告指标仍是 N/A，样例中甚至出现占位数值。
- **根因**：Research Agent 虽可调 SEC Company Facts，但 ResearchPack 不携带 facts；Analysis Agent 只有 concept 选择和计算工具，没有任何真实数值输入。
- **修复**：CIK 解析后并行预取 submissions、Company Facts 和搜索；Company Facts 用 `filed <= as_of_date` 防止未来数据，再按版本化 concept mapping 选择最近两个可比期，将带单位/期间/locator 的 JSON 注入 Analysis Task。
- **验证**：新增 filed-date 截断、prefetch 缓存/预算/统计、Crew inputs 注入的离线测试。真实数字输出需在 P06-07 受控 live smoke 中最终确认。
- **面试要点**：“模型不编数”不能只写提示词；先由确定性程序选数和截断，再让 Agent 负责解释。

## 16. Windows WMI 查询卡死导致 Python/PowerShell “无输出”

- **现象**：`platform.system()` / `platform.machine()` 长时间无返回，导入 SQLAlchemy、Prometheus 或 Celery 时 pytest 看似随机卡死；`Get-CimInstance` 也卡住。
- **根因范围**：`Winmgmt` 服务虽为 Running，但 WMI 查询链路异常；这是 Windows 主机环境问题，不是投研业务代码或数据库逻辑问题。
- **本轮处理**：只在测试启动器中为两个纯系统信息函数提供常量，使离线业务测试可继续；没有在产品代码中隐藏或绕过 WMI 故障。
- **后续**：P06-06 安装 Docker Desktop/WSL2 前，先修复 Windows 管理服务与 PowerShell 环境；否则 Docker Desktop 安装与 WSL 后端也可能不稳定。

## 附：一个被误会的“问题”——DeepSeek 上下文缓存 0% 命中

- 现象：某请求缓存命中率 0%，怀疑缓存被清。
- 结论：agent 循环中每轮新增内容（新消息/工具结果）必然 miss，命中率 ~80% 属正常健康水平；0% 通常意味着 TTL 过期或系统提示中有动态段（每轮更新的 runtime context）打断前缀——不是缓存清理或插件问题。
- 面试要点：理解缓存命中率口径（固定前缀 ÷ 总输入），才能正确解读监控指标。

---

## 面试速查：高频考点

1. **SQLite 掩盖数据库语义差异** → DB 测试必须用真实 PostgreSQL（外键/约束/事务）。
2. **UOW 刷新顺序** → flush() 显式控制父子表落库顺序。
3. **agent 上下文复利膨胀** → 工具结果瘦身 + 轮次上限联动调参。
4. **状态机兜底** → 异常必须映射到终态（failed），不能悬在 running。
5. **schema 校验** → 业务边界情况（空 facts、负值）要允许，约束来自领域语义。
6. **协议层 vs 业务层** → HTTP 头 ASCII、URL 契约、日期口径（财年 vs 截止日）。
7. **不要用加大超时掩盖性能根因** → 先量化步骤耗时，再修根因。
