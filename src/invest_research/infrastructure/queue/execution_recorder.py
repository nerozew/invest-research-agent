"""Worker 执行后的落库记录：workflow_steps + artifacts 登记（P05.5-opt + P06-06B）。

背景：P04-07 最小 worker 只翻转任务状态，API 的步骤/工件/耗时接口读不到数据。
本模块在 flow 运行成功后：
- 从 ResearchFlowState 推导 00-07 步骤（状态 + 版本摘要）；
- 把 live runner 写到 <artifact_root>/<company>_<as_of>/ 的产物移动到
  <artifact_root>/<job_id>/（与 SqlArtifactContentStore 的解析布局一致），
  并登记 artifacts 表（key/type/checksum/byte_size）。

P06-06B：步骤在 Worker 运行时已由 SqlProgressSink 幂等创建并实时更新状态。
本模块不再重复插入步骤，而是按 (job_id, step_name) 更新/补全：
- 已存在的步骤：只补写结构化 I/O 与 output_schema_version（不覆盖运行中状态）；
- 不存在的步骤（如旧版本跳过创建）：按终态补全（attempt_count=1）。
这一步保证 ExecutionRecorder 与实时进度并存，不冲突、不重复。

不依赖 Celery/CrewAI，可独立离线测试；worker.py 组装时使用。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from invest_research.domain.models import ResearchRequest
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.db.models import Artifact, WorkflowStep
from invest_research.infrastructure.db.repositories import SessionFactory

_LOGGER = logging.getLogger(__name__)

# 产物文件 → artifact_type（与 LiveResearchFlowRunner._persist_intermediates 的键一致）
_ARTIFACT_TYPES: dict[str, str] = {
    "00_request.json": "request",
    "02_research_pack.json": "research_pack",
    "04_financial_analysis_pack.json": "analysis_pack",
    "05_report_draft.json": "report_draft",
    "06_quality_report.json": "quality_report",
    "07_manifest.json": "manifest",
    "08_report.md": "final_report_markdown",
    "09_report.pdf": "final_report_pdf",
}

# 兼容迁移：旧版 live runner 写到 company_as_of 目录的键（不含 08/09）
_LEGACY_ARTIFACT_TYPES: dict[str, str] = {
    "00_request.json": "request",
    "02_research_pack.json": "research_pack",
    "04_financial_analysis_pack.json": "analysis_pack",
    "05_report_draft.json": "report_draft",
    "06_quality_report.json": "quality_report",
    "07_manifest.json": "manifest",
}

# (步骤名, sequence_no, state 字段名)
_STEP_SPECS: tuple[tuple[str, int, str], ...] = (
    ("00_request", 0, "request"),
    ("01_company_resolve", 1, "company_identity"),
    ("02_research", 2, "research_pack"),
    ("03_documents", 3, "document_manifest"),
    ("04_analysis", 4, "analysis_pack"),
    ("05_writer", 5, "report_draft"),
    ("06_quality_gate", 6, "quality_report"),
    ("07_manifest", 7, "run_manifest"),
)


def derive_steps(state: ResearchFlowState) -> list[dict[str, object]]:
    """从 Flow state 推导 00-07 步骤记录（status + 版本摘要）。"""
    steps: list[dict[str, object]] = []

    def _pack_version(pack: object | None) -> str | None:
        return getattr(pack, "version", None) if pack is not None else None

    for name, seq, attr in _STEP_SPECS:
        value = getattr(state, attr, None)
        if attr == "document_manifest":
            ok = bool(value)
        elif attr == "run_manifest":
            ok = isinstance(value, dict) and value.get("status") == "published"
        elif attr == "quality_report":
            ok = bool(value) and bool(getattr(value, "all_passed", False))
        else:
            ok = value is not None
        step: dict[str, object] = {
            "step_name": name,
            "sequence_no": seq,
            "status": "succeeded" if ok else "failed",
            "attempt_count": 1,
            "input_json": {},
            "output_json": {},
            "error_json": {},
            "output_schema_version": _pack_version(value),
        }
        if not ok and attr == "quality_report" and value is not None:
            step["error_json"] = {"recommendation": getattr(value, "recommendation", None)}
        if attr == "run_manifest" and isinstance(value, dict):
            step["output_json"] = {"status": value.get("status")}
        steps.append(step)
    return steps


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_job_dir(job_dir: Path, types: dict[str, str]) -> list[dict[str, object]]:
    """扫描 job 目录下白名单键的工件，返回登记数据（不写 DB）。"""
    artifacts: list[dict[str, object]] = []
    for key, artifact_type in types.items():
        path = job_dir / key
        if not path.is_file():
            continue
        artifacts.append(
            {
                "artifact_key": key,
                "artifact_type": artifact_type,
                "storage_uri": f"{job_dir.name}/{key}",
                "content_checksum": _sha256(path),
                "byte_size": path.stat().st_size,
            }
        )
    return artifacts


def merge_artifacts_to_job_dir(
    artifact_root: str, job_id: uuid.UUID, request: ResearchRequest
) -> list[dict[str, object]]:
    """把工件统一收口到 <artifact_root>/<job_id>/ 并返回登记数据。

    - 存在旧版 <company>_<as_of> 目录时合并迁移 00-07 文件到 job 目录；
    - job 目录已存在（fake 已写入 00-07 / Publisher 已写入 08/09）时只补扫；
    - 返回 job 目录白名单内全部 00-09 文件登记元数据。
    未找到任何目录时返回空列表。
    """
    root = Path(artifact_root).resolve()
    src = root / f"{request.input_company}_{request.as_of_date.isoformat()}"
    dst = root / str(job_id)

    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        moved = False
        for key, _ in _LEGACY_ARTIFACT_TYPES.items():
            src_file = src / key
            dst_file = dst / key
            if src_file.is_file() and not dst_file.exists():
                shutil.move(str(src_file), str(dst_file))
                moved = True
        if moved:
            _LOGGER.info("已把旧版工件迁移到 job 目录: %s -> %s", src, dst)
    elif not dst.is_dir():
        return []

    return _scan_job_dir(dst, _ARTIFACT_TYPES)


class ExecutionRecorder:
    """把已成功的执行结果落库（workflow_steps 更新/补全 + artifacts 目录登记）。"""

    def __init__(self, session_factory: SessionFactory, artifact_root: str) -> None:
        self._sf = session_factory
        self._artifact_root = artifact_root

    def record(self, job_id: uuid.UUID, state: ResearchFlowState | None) -> None:
        if state is None:
            return
        steps = derive_steps(state)
        artifacts: list[dict[str, object]] = []
        if state.request is not None:
            try:
                artifacts = merge_artifacts_to_job_dir(self._artifact_root, job_id, state.request)
            except OSError as exc:
                _LOGGER.warning("移动/登记工件失败 job=%s: %s", job_id, exc)
        with self._sf() as session:
            existing: dict[str, WorkflowStep] = {
                s.step_name: s
                for s in session.query(WorkflowStep)
                .filter(WorkflowStep.job_id == job_id)
                .all()
            }
            for step in steps:
                name = str(step["step_name"])
                row = existing.get(name)
                if row is not None:
                    # P06-06B：步骤已由 SqlProgressSink 实时创建/更新。
                    # 这里只补写结构化 I/O 与版本摘要，不覆盖运行中状态/时间。
                    attempt_count = step.get("attempt_count")
                    if isinstance(attempt_count, int):
                        row.attempt_count = max(int(row.attempt_count or 0), attempt_count)
                    input_json = step.get("input_json")
                    if isinstance(input_json, dict):
                        row.input_json = {**row.input_json, **input_json}
                    output_json = step.get("output_json")
                    if isinstance(output_json, dict):
                        row.output_json = {**row.output_json, **output_json}
                    output_schema_version = step.get("output_schema_version")
                    if isinstance(output_schema_version, str):
                        row.output_schema_version = output_schema_version
                    error_json = step.get("error_json")
                    if isinstance(error_json, dict) and not row.error_json:
                        row.error_json = error_json
                    continue
                # 旧任务无实时步骤：按终态补全（attempt_count=1）
                session.add(WorkflowStep(id=uuid.uuid4(), job_id=job_id, **step))
            # P06-07 前置修复：幂等登记——已存在的 (job_id, artifact_key) 跳过。
            if artifacts:
                from sqlalchemy import select

                existing_keys = set(
                    session.execute(
                        select(Artifact.artifact_key).where(Artifact.job_id == job_id)
                    )
                    .scalars()
                    .all()
                )
                for artifact in artifacts:
                    key = str(artifact["artifact_key"])
                    if key in existing_keys:
                        continue
                    session.add(Artifact(id=uuid.uuid4(), job_id=job_id, **artifact))
            session.commit()
