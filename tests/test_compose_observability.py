"""P06-06 Docker Compose observability profile 静态校验测试（离线）。

验证目标（docs/05 P06-06）：
- observability profile 包含 Prometheus / Grafana / OTel Collector / Jaeger；
- 所有观测镜像使用明确版本（不使用 latest）；
- observability 服务配置了健康检查、depends_on、持久卷和必要端口；
- 基础服务保留且 api/worker 已配置 OTEL_SERVICE_NAME 与 OTLP 端点；
- Prometheus / Grafana datasource / OTel collector 配置文件可解析且结构正确。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

OBSERVABILITY_SERVICES = (
    "prometheus",
    "grafana",
    "otel-collector",
    "jaeger",
)
BASE_SERVICES = (
    "postgres",
    "redis",
    "migrate",
    "api",
    "worker",
    "streamlit",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    path = PROJECT_ROOT / "compose.yml"
    assert path.exists(), "compose.yml 必须存在"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded is not None
    return loaded


@pytest.fixture(scope="module")
def collector_config() -> dict:
    path = PROJECT_ROOT / "deploy" / "otel-collector.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded is not None
    return loaded


# ---- 1. profile 服务与基础服务 ----

def test_observability_profile_services_present(compose: dict) -> None:
    services = compose["services"]
    for name in OBSERVABILITY_SERVICES:
        assert name in services, f"缺少 observability 服务: {name}"
        assert services[name].get("profiles") == ["observability"], (
            f"{name} 必须属于 observability profile"
        )


def test_base_services_preserved(compose: dict) -> None:
    services = compose["services"]
    for name in BASE_SERVICES:
        assert name in services, f"基础服务被移除: {name}"


def test_observability_images_use_pinned_versions(compose: dict) -> None:
    """所有观测镜像必须使用明确版本，不允许 latest。"""
    services = compose["services"]
    for name in OBSERVABILITY_SERVICES:
        image = services[name]["image"]
        assert ":" in image and not image.endswith(":latest"), (
            f"{name} 镜像必须使用明确版本: {image}"
        )


# ---- 2. 健康检查 / depends_on / 端口 / 卷 ----

def test_observability_services_have_healthchecks(compose: dict) -> None:
    services = compose["services"]
    for name in OBSERVABILITY_SERVICES:
        assert "healthcheck" in services[name], f"{name} 缺少 healthcheck"


def test_observability_services_have_depends_on_and_ports(compose: dict) -> None:
    services = compose["services"]
    # Prometheus → API；Grafana → Prometheus；Collector → Jaeger
    expected_deps = {
        "prometheus": "api",
        "grafana": "prometheus",
        "otel-collector": "jaeger",
    }
    for name, dep in expected_deps.items():
        assert dep in services[name]["depends_on"], f"{name} 缺少 depends_on {dep}"
        assert "ports" in services[name], f"{name} 缺少端口映射"

    # Jaeger 有查询 UI 与 OTLP gRPC 端口
    assert "16686:16686" in services["jaeger"]["ports"]
    assert "4317:4317" in services["jaeger"]["ports"]


def test_observability_services_have_volumes(compose: dict) -> None:
    services = compose["services"]
    # Prometheus / Grafana 需要持久卷；collector 挂载配置文件
    assert "prometheus_data:/prometheus" in services["prometheus"]["volumes"]
    assert "grafana_data:/var/lib/grafana" in services["grafana"]["volumes"]
    assert any("otel-collector.yaml:" in v for v in services["otel-collector"]["volumes"])
    assert "prometheus_data" in compose["volumes"]
    assert "grafana_data" in compose["volumes"]


# ---- 3. 应用 OTEL 环境变量 ----

def test_api_and_worker_have_otel_env(compose: dict) -> None:
    services = compose["services"]
    api_env = services["api"]["environment"]
    worker_env = services["worker"]["environment"]
    assert api_env.get("OTEL_SERVICE_NAME") == "invest-research-api"
    assert worker_env.get("OTEL_SERVICE_NAME") == "invest-research-worker"
    # 共同基线含 OTLP 端点（默认指向 collector 服务名，可被环境变量覆盖）
    shared = compose["x-app-env"]
    assert shared.get("OTEL_EXPORTER_OTLP_ENDPOINT") == (
        "${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4318}"
    )


def test_flow_mode_defaults_to_fake(compose: dict) -> None:
    """P06-06 要求默认 FLOW_MODE=fake，禁止调用真实 LLM/Serper/付费 API。"""
    assert compose["x-app-env"]["FLOW_MODE"] == "${FLOW_MODE:-fake}"


# ---- 4. 部署配置文件 ----

def test_prometheus_config_parses() -> None:
    path = PROJECT_ROOT / "deploy" / "prometheus" / "prometheus.yml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded is not None
    jobs = loaded["scrape_configs"]
    assert len(jobs) == 1
    assert jobs[0]["job_name"] == "invest-research-api"
    assert jobs[0]["metrics_path"] == "/metrics"
    assert jobs[0]["static_configs"][0]["targets"] == ["api:8000"]


def test_grafana_datasource_provisioning() -> None:
    path = PROJECT_ROOT / "deploy" / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    ds = loaded["datasources"][0]
    assert ds["uid"] == "prometheus"
    assert ds["type"] == "prometheus"
    assert ds["url"] == "http://prometheus:9090"
    assert ds["isDefault"] is True


def test_grafana_dashboard_provider_provisioning() -> None:
    path = PROJECT_ROOT / "deploy" / "grafana" / "provisioning" / "dashboards" / "dashboard.yml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    provider = loaded["providers"][0]
    assert provider["type"] == "file"
    assert provider["options"]["path"] == "/etc/grafana/provisioning/dashboards"


def test_collector_exports_to_jaeger_and_health(collector_config: dict) -> None:
    """collector 保留 debug 导出，同时转发 Jaeger，并启用 health_check。"""
    traces = collector_config["service"]["pipelines"]["traces"]
    assert "debug" in traces["exporters"]
    assert "otlp/jaeger" in traces["exporters"]
    assert "health_check" in collector_config["service"]["extensions"]
    # health_check 必须监听 0.0.0.0:13133——容器内 localhost 无法被宿主机 port mapping 转发
    health = collector_config["extensions"]["health_check"]
    assert health["endpoint"] == "0.0.0.0:13133"
    otlp_jaeger = collector_config["exporters"]["otlp/jaeger"]
    assert otlp_jaeger["endpoint"] == "jaeger:4317"
    assert otlp_jaeger["tls"]["insecure"] is True
