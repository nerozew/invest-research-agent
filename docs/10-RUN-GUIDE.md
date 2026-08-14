# 本地一键启动指南（Phase 4）

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
# 在项目根目录执行（自动 pull GHCR 最新 phase4 镜像 + compose up + 健康检查）
bash scripts/deploy_phase4.sh
```

等价的手工命令：

```bash
# 在项目根目录（含 compose.yml）
export INVEST_RESEARCH_IMAGE=ghcr.io/nerozew/invest-research-agent:phase4
docker pull "$INVEST_RESEARCH_IMAGE"
docker compose up -d --force-recreate --remove-orphans
```

> 若未设置 `INVEST_RESEARCH_IMAGE`，compose 会回退到 `invest-research:phase4`
> （即 `docker compose build` 产出的同名镜像，用于本地开发调试）。

启动后 compose 会自动：
1. 等 postgres/redis healthy；
2. 跑一次性 `migrate`（`alembic upgrade head`，建 0001–0005 表）；
3. 再启动 api / worker / streamlit。

## 2b. 备用：断网 / GHCR 不可达时（artifact + docker load）

> 仅当 `docker pull ghcr.io/...` 不可用时才需要。通常不需要，也尽量避免：
> GitHub Actions 每个 run 的 `invest-research-phase4-image` artifact 是当时构建的镜像快照。

```bash
# 1) 在 GitHub Actions 页面下载最新 run 的 invest-research-phase4-image artifact
#    （zip 内含 invest-research-phase4.tar.gz）
# 2) 解压后上传到部署机，然后：
gzip -d -c invest-research-phase4.tar.gz > invest-research-phase4.tar
docker load -i invest-research-phase4.tar
docker tag ghcr.io/nerozew/invest-research-agent:phase4 invest-research:phase4
# 3) 启动（非 GHCR 镜像，INVEST_RESEARCH_IMAGE 可不设，用回退名）
docker compose up -d
```

## 3. 访问地址

| 服务 | 地址 | 说明 |
|---|---|---|
| Streamlit 前端 | http://localhost:8501 | 创建任务 → 展示 job_id → 轮询状态 → 工件 |
| FastAPI | http://localhost:8000 | API；`/health`、`/readiness`、`/docs`(Swagger) |
| PostgreSQL / Redis | 容器内部互连 | 未对外暴露端口（安全默认） |

## 4. 验证一条命令跑通

```bash
# 查看服务状态（全部应为 healthy / Up）
docker compose ps

# API 探活
curl http://localhost:8000/health
curl http://localhost:8000/readiness

# 创建任务（真实落库）
curl -X POST http://localhost:8000/v1/research-jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-1" \
  -d '{"input_company":"Apple Inc.","as_of_date":"2024-12-31","language":"zh-CN","requested_forms":["10-K"]}'

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

## 6. 已知限制（Phase 4）

- 数据库提交成功但 Celery 投递失败之间存在窗口（无 transactional outbox，计划 P05-03 处理）。
- Worker 当前用 fake Flow（`ResearchFlowRunner`，P03 全链离线演练），不调用真实付费模型。
- CLI 演示：`python -m invest_research.cli --api-base http://localhost:8000 status <job_id>`

### 6.1 单用户边界（P04-UI-06~10 起）

> 当前为**单用户本地部署**：任务列表会展示该实例中的**全部任务**。
> 暂无用户认证与多用户数据隔离；**不应将该系统直接暴露到不可信公网**。
> 若需对外提供，请务必先添加认证、授权与多用户隔离（不在本阶段范围）。
