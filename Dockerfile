# syntax=docker/dockerfile:1
# P04-09：multi-stage Dockerfile。API / Worker / Streamlit 共用同一镜像，以不同启动命令运行。
# 由 GitHub Actions（.github/workflows/docker-image.yml）在 hosted runner 上构建并推送 GHCR。

# ---------- 阶段 1：构建依赖 ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    # 大依赖（chromadb/pyarrow/pymupdf 等）下载需要更长超时，避免慢速网络超时失败。
    # 不指定 UV_DEFAULT_INDEX：由构建环境（CI/本地）决定源，仓库不写死国内镜像。
    UV_HTTP_TIMEOUT=600 \
    # 合理并发，避免在带宽受限环境里打满连接。
    UV_CONCURRENT_DOWNLOADS=4

# uv 官方安装（版本固定，可复现）
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

WORKDIR /app

# 先复制依赖清单以利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 只安装运行时依赖（不装 dev），生成虚拟环境。
# BuildKit cache mount 让 uv 缓存跨构建复用，显著减少重复下载。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- 阶段 2：运行镜像（非 root） ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 非 root 用户：最小权限（无 shell 的专用用户 app）
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# 复制 builder 阶段安装好的虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制源码（src 布局）+ 前端页面 + 迁移
COPY src ./src
COPY frontend ./frontend
COPY migrations ./migrations
COPY alembic.ini ./

# 工件目录（运行时挂载/卷）
RUN mkdir -p /app/artifacts && chown -R app:app /app/artifacts

# 健康检查：默认探活命令，具体服务在 compose 中按角色覆盖
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/bin/sh", "-c", "echo ok"]

USER app

EXPOSE 8000 8501

# 默认：API 服务（其他角色用 docker run / compose command 覆盖）
CMD ["sh", "-c", "uvicorn invest_research.api.app:create_app --factory --host 0.0.0.0 --port 8000"]