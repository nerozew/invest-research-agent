"""P04-11：最小 CLI demo（research run / status / artifacts）。

- 只通过 HTTP 调用 FastAPI（复用 frontend.ResearchApiClient），
  不直接访问数据库、Redis 或 Flow；
- multi-agent：API_BASE_URL 从环境变量读取（默认 http://localhost:8000）；
- run：创建任务（自动生成 Idempotency-Key）→ 有限轮询到终态；
- status：查询任务状态（含步骤耗时/错误码）；
- artifacts：列出 job 已登记工件。

错误处理：统一捕获 ResearchApiClient 抛出的分类错误（超时/网络/4xx/5xx），
打印可读消息，不泄露连接串/密钥/内部路径。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from typing import NoReturn

from invest_research.domain.status import JobStatus
from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.errors import ApiError, ApiNetworkError, ApiTimeoutError
from invest_research.frontend.models import CreateResearchJobRequest, JobSnapshot

__all__ = ["build_parser", "main"]

# 有限轮询：最多等待秒数 / 每次间隔（对齐 UI 使用的保守上限）。
_MAX_WAIT_SECONDS = 300
_POLL_INTERVAL = 5

# 终态：到达即停止轮询。
_TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.PARTIAL,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def _default_api_base() -> str:
    """API 基址：环境变量 API_BASE_URL；缺省 localhost。"""
    return os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")


def _new_client(api_base: str | None) -> ResearchApiClient:
    return ResearchApiClient(base_url=api_base or _default_api_base(), timeout=15.0)


def _print_job(snapshot: JobSnapshot) -> None:
    """打印任务快照（状态/当前步骤/错误/耗时/步骤）。"""
    print(f"job_id:         {snapshot.job_id}")
    print(f"status:         {snapshot.status.value}")
    print(f"current_step:   {snapshot.current_step or '-'}")
    if snapshot.error_code:
        print(f"error_code:     {snapshot.error_code}")
    if snapshot.error_message:
        print(f"error_message:  {snapshot.error_message}")
    if snapshot.duration_seconds is not None:
        print(f"duration_sec:   {snapshot.duration_seconds}")
    for step in snapshot.steps:
        print(
            f"  step={step.step_name!r} seq={step.sequence_no} "
            f"status={step.status.value} attempts={step.attempt_count}"
        )


def _poll_until_terminal(client: ResearchApiClient, job_id: str) -> JobSnapshot:
    """有限轮询到终态；超时返回当前快照（不抛错）。"""
    elapsed = 0
    while elapsed < _MAX_WAIT_SECONDS:
        snapshot = client.get_research_job(job_id)
        if JobStatus(snapshot.status) in _TERMINAL_STATUSES:
            return snapshot
        time.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
    # 未在期限内到终态：返回最后一次快照，调用方提示"仍在运行/超时"。
    return client.get_research_job(job_id)


def _cmd_run(args: argparse.Namespace) -> NoReturn:
    client = _new_client(args.api_base)
    request = CreateResearchJobRequest(
        input_company=args.company,
        as_of_date=args.as_of_date,
        language=args.language,
        requested_forms=tuple(args.forms or ["10-K", "10-Q"]),
    )
    # 每次 run 生成新的 Idempotency-Key（真正的新任务）。
    idempotency_key = f"cli-{uuid.uuid4()}"
    created = client.create_research_job(request=request, idempotency_key=idempotency_key)
    print(f"created job_id={created.job_id} status={created.status.value}")
    print(f"idempotency_key={idempotency_key}")

    if not args.no_wait:
        snapshot = _poll_until_terminal(client, str(created.job_id))
        _print_job(snapshot)
    sys.exit(0)


def _cmd_status(args: argparse.Namespace) -> NoReturn:
    client = _new_client(args.api_base)
    snapshot = client.get_research_job(args.job_id)
    _print_job(snapshot)
    sys.exit(0)


def _cmd_artifacts(args: argparse.Namespace) -> NoReturn:
    client = _new_client(args.api_base)
    artifacts = client.list_artifacts(args.job_id)
    if not artifacts:
        print("no artifacts registered for this job")
    for info in artifacts:
        print(
            f"{info.artifact_key}\t{info.artifact_type}\t"
            f"{info.byte_size} bytes\tschema={info.schema_version or '-'}"
        )
    sys.exit(0)


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器（供测试直接调用）。"""
    parser = argparse.ArgumentParser(
        prog="research", description="Agent 驱动的自动化投研系统 CLI（只通过 FastAPI）"
    )
    parser.add_argument("--api-base", default=None, help="FastAPI 基址（默认走 API_BASE_URL）")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="创建投研任务并轮询到终态")
    run.add_argument("company", help="公司名或 ticker")
    run.add_argument("--as-of-date", default=None, help="数据截止日（YYYY-MM-DD），默认今天")
    run.add_argument("--language", default="zh-CN", choices=["zh-CN", "en"])
    run.add_argument("--forms", nargs="*", default=None, help="SEC 表单类型（默认 10-K 10-Q）")
    run.add_argument("--no-wait", action="store_true", help="创建后不轮询，直接退出")
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="查询任务状态")
    status.add_argument("job_id", help="任务 UUID")
    status.set_defaults(func=_cmd_status)

    artifacts = sub.add_parser("artifacts", help="列出任务工件")
    artifacts.add_argument("job_id", help="任务 UUID")
    artifacts.set_defaults(func=_cmd_artifacts)

    return parser


def main(argv: list[str] | None = None) -> NoReturn:
    """CLI 入口（供 ``uv run research`` 调用）。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "as_of_date", None) is None and hasattr(args, "as_of_date"):
        from datetime import date

        args.as_of_date = date.today().isoformat()
    try:
        args.func(args)
    except ApiTimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ApiNetworkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # 边界：兜底可读错误，不泄露内部路径
        print(f"error: 处理请求失败（{type(exc).__name__}）", file=sys.stderr)
        sys.exit(2)
    # 成功路径也显式退出，保证 NoReturn 语义（不隐式 return）
    sys.exit(0)


if __name__ == "__main__":
    main()
