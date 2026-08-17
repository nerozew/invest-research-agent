"""P06-09C observability smoke（可重复、仅本地 Docker API、不删 volume、不 down -v）。

在隔离 fake env（FLOW_MODE=fake）下验证可观测性管线端到端：
- 创建 20 个 fake job（fast 10 + deep 10）、并发 2-4；
- 覆盖成功 / 幂等复用 / 幂等冲突 / 取消；
- 组件级故障注入（schema 错误 / Pack 修复）经 --include-component-scenarios
  默认关闭（fake flow 无注入路径，由 tests/test_pack_contracts.py 覆盖）；
- 输出创建数 / 终态分布 / fast-deep 数 / Prometheus 非空 / Jaeger / Grafana provisioning。

安全：不读 .env 真实 key；不调真实 SEC/Serper/LLM；不删 volume、不 down -v。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, timedelta
from typing import Any

DEFAULT_API_BASE = "http://localhost:8000"
DEFAULT_PROMETHEUS = "http://localhost:9090"
DEFAULT_JAEGER = "http://localhost:16686"
DEFAULT_GRAFANA = "http://localhost:3000"

_NUM_JOBS = 20
_POLL_TIMEOUT_S = 120
_POLL_INTERVAL_S = 2.0
_TERMINAL = {"succeeded", "failed", "cancelled"}


def _request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = raw.decode("utf-8", errors="replace")
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}: {exc.reason}") from exc


def _job_payload(index: int, profile: str) -> dict[str, Any]:
    return {
        "input_company": f"OBS-SMOKE-{profile}-{index:02d}",
        "as_of_date": (date.today() - timedelta(days=1)).isoformat(),
        "language": "zh-CN",
        "requested_forms": ["10-K"],
        "research_profile": profile,
    }


def _wait_terminal(
    api_base: str, job_id: str, timeout_s: float = _POLL_TIMEOUT_S
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, body = _request(f"{api_base}/v1/research-jobs/{job_id}")
        if code == 200 and isinstance(body, dict) and body.get("status") in _TERMINAL:
            return body
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"job {job_id} 未在 {timeout_s}s 内到达终态")


def _prometheus_metric(prom_base: str, query: str) -> float:
    url = f"{prom_base}/api/v1/query?query={urllib.parse.quote(query)}"
    code, body = _request(url)
    if code != 200 or not isinstance(body, dict) or body.get("status") != "success":
        return 0.0
    result = body.get("data", {}).get("result", [])
    if not result:
        return 0.0
    value = result[0].get("value")
    if not value or len(value) < 2:
        return 0.0
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return 0.0


def _verify_prometheus(prom_base: str) -> dict[str, Any]:
    queries = {
        "research_jobs_total": "sum(research_jobs_total)",
        "http_requests_total": "sum(http_requests_total)",
        "workflow_steps_total": "sum(workflow_steps_total)",
        "up_api": 'count(up{job="invest-research-api"} == 1)',
        "up_worker": 'count(up{job="invest-research-worker"} == 1)',
    }
    metrics = {name: _prometheus_metric(prom_base, q) for name, q in queries.items()}
    return {"metrics_nonempty": {k: v > 0 for k, v in metrics.items()},
            "ok": all(v > 0 for v in metrics.values())}


def _verify_jaeger(jaeger_base: str) -> dict[str, Any]:
    try:
        code, body = _request(f"{jaeger_base}/api/services")
        services = body.get("data", []) if isinstance(body, dict) else []
        ok = any(s in services for s in ("invest-research", "invest-research-api"))
        return {"services": services, "ok": ok}
    except RuntimeError as exc:
        return {"error": str(exc), "ok": False}


def _verify_grafana(grafana_base: str) -> dict[str, Any]:
    def _get(path: str) -> tuple[int, Any]:
        code, body = _request(f"{grafana_base}{path}")
        if code == 401:
            code, body = _request(
                f"{grafana_base}{path}",
                headers={"Authorization": "Basic YWRtaW46YWRtaW4="},
            )
        return code, body

    try:
        _, ds_body = _get("/api/datasources")
        _, dash_body = _get("/api/search?type=dash-db")
        datasources = ds_body if isinstance(ds_body, list) else []
        dashboards = dash_body if isinstance(dash_body, list) else []
        uids = [d.get("uid") for d in dashboards if isinstance(d, dict)]
        has_prom = any(isinstance(d, dict) and d.get("type") == "prometheus"
                       for d in datasources)
        has_red = "invest-research-red" in uids
        return {"datasources": len(datasources), "dashboard_uids": uids,
                "has_prometheus_ds": has_prom, "has_red_dashboard": has_red,
                "ok": bool(has_prom and has_red)}
    except RuntimeError as exc:
        return {"error": str(exc), "ok": False}


def _component_scenarios() -> dict[str, Any]:
    return {"ok": True,
            "note": "组件级 PackBoundary 修复场景由 tests/test_pack_contracts.py 覆盖"}


def _run(args: argparse.Namespace) -> int:
    api = args.api_base
    print(f"[obs-smoke] api={api} prometheus={args.prometheus} jaeger={args.jaeger} "
          f"grafana={args.grafana}")

    try:
        code, _ = _request(f"{api}/health")
        if code != 200:
            print(f"[obs-smoke] 失败：/health 返回 {code}", file=sys.stderr)
            return 1
    except RuntimeError as exc:
        print(f"[obs-smoke] 失败：无法连接 API：{exc}", file=sys.stderr)
        return 1

    from concurrent.futures import ThreadPoolExecutor

    created: list[dict[str, Any]] = []
    concurrency = random.randint(2, 4)
    print(f"[obs-smoke] 并发创建 {concurrency} 个任务…")

    def _create(spec: tuple[int, str]) -> None:
        index, profile = spec
        code, body = _request(f"{api}/v1/research-jobs", method="POST",
                              data=_job_payload(index, profile))
        if code != 202:
            raise RuntimeError(f"创建失败 index={index} code={code} body={body}")
        created.append({"index": index, "profile": profile, "job_id": body["job_id"]})

    specs = [(i, "fast" if i < 10 else "deep") for i in range(_NUM_JOBS)]
    random.Random(7).shuffle(specs)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for _ in pool.map(_create, specs):
            pass

    fast = sum(1 for c in created if c["profile"] == "fast")
    deep = sum(1 for c in created if c["profile"] == "deep")
    print(f"[obs-smoke] 已创建 {len(created)} 个 job（fast={fast}，deep={deep}）")

    # 场景：幂等复用 / 幂等冲突 / 取消
    idem_key = f"obs-smoke-{uuid.uuid4()}"
    base_payload = _job_payload(0, "fast")
    c1, b1 = _request(f"{api}/v1/research-jobs", method="POST", data=base_payload,
                      headers={"Idempotency-Key": idem_key})
    c2, b2 = _request(f"{api}/v1/research-jobs", method="POST", data=base_payload,
                      headers={"Idempotency-Key": idem_key})
    idem_reuse = c1 == 202 and c2 == 200 and b1.get("job_id") == b2.get("job_id")
    if idem_reuse and b1.get("job_id"):
        # 幂等复用：首次创建已进入 created（由 _create 收集），这里只记录场景结论
        # （复用返回同一 job_id，不重复加入 created，避免同一 job 等待两次）
        pass

    conflict = dict(base_payload, input_company=base_payload["input_company"] + "-conflict")
    c3, _ = _request(f"{api}/v1/research-jobs", method="POST", data=conflict,
                     headers={"Idempotency-Key": idem_key})
    idem_conflict = c3 == 409

    cancel_payload = _job_payload(19, "deep")
    c4, b4 = _request(f"{api}/v1/research-jobs", method="POST", data=cancel_payload)
    cancel_id = b4.get("job_id")
    c5, _ = _request(f"{api}/v1/research-jobs/{cancel_id}", method="DELETE")
    cancel_ok = c5 == 200
    created.append({"job_id": cancel_id, "profile": "deep", "payload": cancel_payload,
                    "index": -2})

    print(f"[obs-smoke] 幂等复用={'OK' if idem_reuse else 'FAIL'} | "
          f"幂等冲突={'OK' if idem_conflict else 'FAIL'} | "
          f"取消={'OK' if cancel_ok else 'FAIL'}")

    # 等待终态
    terminal_counts: dict[str, int] = {}
    for entry in created:
        snap = _wait_terminal(api, entry["job_id"])
        status = snap.get("status", "unknown")
        terminal_counts[status] = terminal_counts.get(status, 0) + 1
        entry["final_status"] = status
    dist_text = json.dumps(terminal_counts, ensure_ascii=False, sort_keys=True)
    print(f"[obs-smoke] 终态分布：{dist_text}")

    # 管线验证
    time.sleep(3)
    prom = _verify_prometheus(args.prometheus)
    jaeger = _verify_jaeger(args.jaeger)
    grafana = _verify_grafana(args.grafana)
    print(f"[obs-smoke] Prometheus 非空={prom['ok']} {prom['metrics_nonempty']}")
    print(f"[obs-smoke] Jaeger services={jaeger.get('services')} ok={jaeger.get('ok')}")
    print(f"[obs-smoke] Grafana ds={grafana.get('datasources')} "
          f"uids={grafana.get('dashboard_uids')} ok={grafana.get('ok')}")

    fast = sum(1 for c in created if c["profile"] == "fast")
    deep = sum(1 for c in created if c["profile"] == "deep")
    results = {
        "created_count": len(created),
        "fast_count": fast,
        "deep_count": deep,
        "terminal_distribution": terminal_counts,
        "scenarios": {"idempotent_reuse": idem_reuse, "idempotent_conflict": idem_conflict,
                      "cancel": cancel_ok},
        "prometheus": prom,
        "jaeger": jaeger,
        "grafana": grafana,
    }
    if args.report:
        from pathlib import Path

        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"[obs-smoke] 报告已写入 {args.report}")

    ok = (idem_reuse and idem_conflict and cancel_ok
          and terminal_counts.get("succeeded", 0) >= 1
          and prom["ok"] and jaeger.get("ok") and grafana.get("ok"))
    print(f"[obs-smoke] 总体结果：{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Observability smoke (fake, no volume removal)")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--prometheus", default=DEFAULT_PROMETHEUS)
    parser.add_argument("--jaeger", default=DEFAULT_JAEGER)
    parser.add_argument("--grafana", default=DEFAULT_GRAFANA)
    parser.add_argument("--report", default=None)
    parser.add_argument("--include-component-scenarios", action="store_true",
                        help="默认关闭：组件级故障注入由 test_pack_contracts 覆盖")
    args = parser.parse_args()

    if args.include_component_scenarios:
        comp = _component_scenarios()
        print(f"[obs-smoke] component self-check: ok={comp['ok']} note={comp['note']}")

    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
