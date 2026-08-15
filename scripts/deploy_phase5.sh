#!/usr/bin/env bash
# Phase 5 一键部署（Ubuntu / 任意带 Docker 的主机）
#
# 默认方式：GitHub Actions 构建推送 GHCR → 本脚本 docker pull + docker compose up。
# 断网备用方案（GHCR 不可达时）：GitHub Actions artifact 下载 → docker load，见文档
# docs/10-RUN-GUIDE.md 的"备用：离线 docker load"一节。
#
# 约束：
# - 不删除 postgres_data / redis_data / artifacts_data（保留数据）
# - 不执行 docker push（发布只发生在 CI）
# - 不打印密钥/.env
set -euo pipefail

cd "$(dirname "$0")/.."

# 发行镜像：GitHub Actions 构建并推送（.github/workflows/docker-image.yml）
GHCR_IMAGE="ghcr.io/nerozew/invest-research-agent:phase5"

# ==== 第 1 步：拉取最新 phase5 镜像 ====
echo "== docker pull ${GHCR_IMAGE} =="
docker pull "${GHCR_IMAGE}"
PULLED_DIGEST="$(docker image inspect "${GHCR_IMAGE}" --format '{{index .RepoDigests 0}}')"
echo "image: ${PULLED_DIGEST}"

# ==== 第 2 步：启动/更新 Compose（保留持久化卷）====
# 强制重建运行容器以应用新镜像；不传 -v，绝不删卷。
export INVEST_RESEARCH_IMAGE="${GHCR_IMAGE}"
echo "== docker compose up -d --force-recreate --remove-orphans =="
docker compose up -d --force-recreate --remove-orphans

# ==== 第 3 步：等待健康并输出状态 ====
sleep 15
echo "== docker compose ps -a =="
docker compose ps -a --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}'

echo "== /health =="
curl -fsS -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8000/health
echo "== /readiness =="
curl -fsS -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8000/readiness
echo "== streamlit /_stcore/health =="
curl -fsS -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8501/_stcore/health

echo "DEPLOY_PHASE5_OK (digest ${PULLED_DIGEST})"