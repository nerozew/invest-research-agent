# 本地一键启动指南（Phase 5）

> 目标：一条命令启动 **API + Worker + Streamlit + PostgreSQL + Redis + migrate**，
> 然后浏览器打开前端做前后端联调。本指南适合你自己在本地验证。

## 1. 前置条件

- 装有 Docker（Windows Docker Desktop 或 Ubuntu Docker）
- 有项目源码（本仓库）
- （可选）准备真实 `.env`：`LLM_API_KEY`、`SEC_USER_AGENT_CONTACT`
  - 不填也能启动：任务创建、查询、取消、工件、UI 联调都可用；
  - 真正执行到 Agent/LLM 环节才需要 key（本阶段 Worker 用 fake Flow，不联网）。

## 2. 一键启动（默认：直接从 GHCR 拉取）

> 推荐的部署方式是 **GitHub Actions 负责 build + push，部署机只执行
> `docker pull` + `docker compose up`**。仓库提供一体化脚本：

```bash
# 在项目根目录执行（自动 pull GHCR 最新 phase5 镜像 + compose up + 健康检查）
bash scripts/deploy_phase5.sh
```

等价的手工命令：

```bash
# 在项目根目录（含 compose.yml）
export INVEST_RESEARCH_IMAGE=ghcr.io/nerozew/invest-research-agent:phase5
docker pull "$INVEST_RESEARCH_IMAGE"
docker compose up -d --force-recreate --remove-orphans
```

> 若未设置 `INVEST_RESEARCH_IMAGE`，compose 会回退到 `invest-research:phase5`
> （即 `docker compose build` 产出的同名镜像，用于本地开发调试）。

启动后 compose 会自动：
1. 等 postgres/redis healthy；
2. 跑一次性 `migrate`（`alembic upgrade head`，建 0001–0005 表）；
3. 再启动 api / worker / streamlit。

## 2b. 备用：断网 / GHCR 不可达时（artifact + docker load）

> 仅当 `docker pull ghcr.io/...` 不可用时才需要。通常不需要，也尽量避免：
> GitHub Actions 每个 run 的 `invest-research-phase5-image` artifact 是当时构建的镜像快照。

```bash
# 1) 在 GitHub Actions 页面下载最新 run 的 invest-research-phase5-image artifact
#    （zip 内含 invest-research-phase5.tar.gz）
# 2) 解压后上传到部署机，然后：
gzip -d -c invest-research-phase5.tar.gz > invest-research-phase5.tar
docker load -i invest-research-phase5.tar
docker tag ghcr.io/nerozew/invest-research-agent:phase5 invest-research:phase5
# 3) 启动（非 GHCR 镜像，INVEST_RESEARCH_IMAGE 可不设，用回退名）
docker compose up -d
```

## 3. 访问地址

| 服务 | 地址 | 说明 |
|---|---|---|
| Streamlit 前端 | http://localhost:8501 | 创建任务 → 展示 job_id → 轮询状态 → 工件 |
| FastAPI | http://localhost:8000 | API；`/health`、`/readiness`、`/docs`(Swagger)。若 `:8000` 被其他服务占用，可用 `compose.port8001.yml` 覆盖映射到 `:8001` |
| PostgreSQL / Redis | 容器内部互连 | 未对外暴露端口（安全默认） |
| Prometheus | http://localhost:9090 | 指标查询/PromQL（targets：`api:8000` 与 `worker:9101`） |
| Grafana | http://localhost:3000 | admin/admin；预置 8 行看板（含 WS3"成本与用量"） |
| Jaeger | http://localhost:16686 | API→Worker→LLM 链路追踪 |
| OTel Collector | localhost:4318 | 应用 OTLP HTTP 导出端点 |

## 4. 验证一条命令跑通

```bash
# 查看服务状态（全部应为 healthy / Up）
docker compose ps

# API 探活
curl http://localhost:8000/health
curl http://localhost:8000/readiness

# 创建任务（真实落库，legacy 路径）
curl -X POST http://localhost:8000/v1/research-jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-1" \
  -d '{"input_company":"Apple Inc.","as_of_date":"2024-12-31","language":"zh-CN","requested_forms":["10-K"]}'

# 创建年度双期间任务（P07 annual_deep：目标/上年 10-K + Company Facts，真实 SEC/DeepSeek）
curl -X POST http://localhost:8000/v1/research-jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-amzn-annual" \
  -d '{"input_company":"AMZN","as_of_date":"2025-10-31","language":"zh-CN",
       "requested_forms":["10-K"],"research_profile":"deep","research_mode":"annual_deep"}'

# 查询任务状态（pending/succeeded 等）
curl http://localhost:8000/v1/research-jobs/<返回的job_id>

# 最近任务列表（created_at 倒序；支持 status/limit/cursor）
curl http://localhost:8000/v1/research-jobs?limit=5
```

## 5. 常用操作

- 停止：`docker compose down`
- 停止并清数据：`docker compose down -v`（会删 postgres_data 等 volume）
- 看日志：`docker compose logs -f api`（或 worker/migrate/streamlit）
- 单独重跑迁移：`docker compose run --rm migrate`

## 6. 已知限制（Phase 5）

- ~~数据库提交与 Celery 投递之间无 outbox~~ —— 已由 **Transactional Outbox**（P05-03B）解决：Job 创建与事件同事务，投递失败持久化重试，重启恢复，防重复投递。
- Worker 执行模式由 `.env` 的 `FLOW_MODE` 控制：`live`（默认，真实 SEC/Serper/DeepSeek，走 `annual_deep` 或 legacy Crew）或 `fake`（全链离线演练，不调真实模型）。
  live 运行需要在部署机 `.env` 提供真实 `LLM_API_KEY`/`SERPER_API_KEY`，且不写入镜像/日志/Git。
- CLI 演示：`python -m invest_research.cli --api-base http://localhost:8000 status <job_id>`

### 6.1 单用户边界（P04-UI-06~10 起）

> 当前为**单用户本地部署**：任务列表会展示该实例中的**全部任务**。
> 暂无用户认证与多用户数据隔离；**不应将该系统直接暴露到不可信公网**。
> 若需对外提供，请务必先添加认证、授权与多用户隔离（不在本阶段范围）。

## 7. 生产配置与密钥管理（P06-04）

### 7.1 配置档位（profile）

- `ENVIRONMENT=development`（默认）：允许占位符密钥，便于本地快速启动演示。
- `ENVIRONMENT=production`：`Settings` 构造时 **fail-fast** 校验——
  `LLM_API_KEY` / `SERPER_API_KEY` / `SEC_USER_AGENT_CONTACT` 仍为占位符
  （`your-*` / `placeholder` / `change-me` 等）时直接抛 `ValidationError`，
  绝不带占位符跑生产。校验逻辑见 `src/invest_research/settings.py`
  （`is_placeholder_secret` + `_production_secrets_guard`），测试见
  `tests/test_secrets_config.py`。

### 7.2 密钥卫生红线（本项目全阶段约束）

1. **密钥只从环境变量 / `.env` 读取**（`SecretStr`），绝不写入代码、日志、工件或 manifest；
2. **`.env` 已被 `.gitignore` 忽略**，`.env.example` 只放占位符；
3. **镜像不带密钥**：`compose.yml` 用 `${VAR:-占位符}` 注入，运行时由宿主机 `.env` 提供；
4. **日志脱敏**（P05-05 structlog redaction）与 **manifest 只记录模型名/计数**（P05.5），
   不含任何密钥或完整 Prompt。

### 7.3 生产部署前的检查清单

```bash
# 1) 准备真实 .env（复制示例后填写真实值，绝不提交）
cp .env.example .env
# 2) 验证 production 配置可构建（真实密钥 + production 档位）
#    （如报占位符错误，说明仍有字段未替换）
uv run python -c "from invest_research.settings import Settings; s = Settings(_env_file='.env'); assert s.environment == 'production'; print('production config OK')"
# 3) 确认仓库不含密钥
git status --porcelain | grep -i env   # 应只出现 .env.example
```

## 8. 可观测性（P06-06 / P06-09C）

### 8.1 启用 observability profile

```bash
# 基础服务 + Prometheus/Grafana/OTel Collector/Jaeger（观测组件按需启动）
docker compose --profile observability up -d
```

| 服务 | 地址 | 说明 |
|---|---|---|
| Prometheus | http://localhost:9090 | targets：api:8000 与 worker:9101（白名单指标） |
| Grafana | http://localhost:3000 | admin/admin；预置 8 行看板（含 WS3"成本与用量"行） |
| Jaeger | http://localhost:16686 | 本地 trace 查看（内存存储，重启丢失） |
| OTel Collector | localhost:4318 | 应用 OTLP HTTP 导出端点 |

### 8.1b Worker 多进程指标（P06-06C + 真实环境修复）

- API（单进程）与 Worker（Celery prefork 多进程）独立采集：`api:8000` 用默认 REGISTRY，`worker:9101` 用 `MultiProcessCollector` 聚合所有子进程写出的 mmap `.db` 文件。
- **必须把 `PROMETHEUS_MULTIPROC_DIR` 作为真实容器环境变量传入**（compose worker 已配 `PROMETHEUS_MULTIPROC_DIR: /tmp/prometheus_metrics`）。仅靠 `worker.py` 内 `os.environ.setdefault` 在 Celery fork 下不可靠，会导致子进程指标不落盘（见 docs/30 事故复盘）。
- WS3 聚合指标：`job_tokens_total` / `job_tool_calls_total` / `job_cost_usd_total`（按 profile/mode 低基数 label）。

### 8.1c WS3 成本估算配置（PRICING_FILE）

每次任务的 token/估算成本落盘依赖单价文件：

```bash
# 1) 单价表（美元每百万 token）已提供
cat deploy/pricing.json
#   {"as_of": "...", "models": {"default": {"input_per_1m": 0.14, "output_per_1m": 0.28}, ...}}

# 2) compose worker 已挂载 + 透传（compose.yml）
#   volumes:      ./deploy/pricing.json:/app/pricing.json:ro
#   environment:  PRICING_FILE: /app/pricing.json

# 3) 本地 CLI 用 .env 指向（worker 容器内由 compose 覆盖为 /app/pricing.json）
#   .env: PRICING_FILE=deploy/pricing.json
```

未配置 `PRICING_FILE` 时成本不估算（`job_cost_usd_total` 为空），token 指标不受影响。Prometheus 打点原理与排障见 [docs/29-PROMETHEUS-GUIDE.md](29-PROMETHEUS-GUIDE.md)。

### 8.2 隔离 fake 栈 observability smoke（P06-09C，可重复）

> 不触碰正在运行的栈：独立 project `obs-smoke`、独立端口/卷/网络，
> FLOW_MODE=fake、key 全为占位符，不调真实 SEC/Serper/LLM，不删 volume、不 down -v。

```bash
# 1) 构建最新镜像（含 P06-09C 代码）
docker build -t invest-research:phase5 .

# 2) 启动隔离 fake 栈（api 18000 / prometheus 19090 / grafana 13000 / jaeger 16687）
docker compose -p obs-smoke -f deploy/compose.observability-smoke.yml \
  --profile observability up -d

# 3) 运行 smoke（20 个 fake job + 幂等复用/冲突/取消 + 三大件数据链验证）
python scripts/run_observability_smoke.py \
  --api-base http://localhost:18000 \
  --prometheus http://localhost:19090 \
  --jaeger http://localhost:16687 \
  --grafana http://localhost:13000 \
  --report reports/obs-smoke.json

# 4) 清理（保留数据卷）
docker compose -p obs-smoke -f deploy/compose.observability-smoke.yml \
  --profile observability down
```

预期：输出 `总体结果：PASS`，报告写入 `reports/obs-smoke.json`；
Prometheus 关键指标（research_jobs_total / http_requests_total / workflow_steps_total）
非空、Jaeger 出现 invest-research-api/worker 服务、Grafana 已加载 invest-research-red dashboard；
worker 日志无 sec.gov/serper.dev/chat/completions 真实外部调用。
