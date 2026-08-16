"""Worker 执行后的落库记录：workflow_steps + artifacts 登记（P05.5-opt）。

背景：P04-07 最小 worker 只翻转任务状态，API 的步骤/工件/耗时接口读不到数据。
本模块在 flow 运行成功后：
- 从 ResearchFlowState 推导 00-07 步骤（状态 + 版本摘要）；
- 把 live runner 写到 <artifact_root>/<company>_<as_of>/ 的产物移动到
  <artifact_root>/<job_id>/（与 SqlArtifactContentStore 的解析布局一致），
  并登记 artifacts 表（key/type/checksum/byte_size）。

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


def move_artifacts_to_job_dir(
    artifact_root: str, job_id: uuid.UUID, request: ResearchRequest
) -> list[dict[str, object]]:
    """把 live 产物的 <company>_<as_of> 目录移到 <job_id> 目录，返回登记数据。

    未找到目录（如 fake 模式无产物）时返回空列表。
    """
    root = Path(artifact_root).resolve()
    src = root / f"{request.input_company}_{request.as_of_date.isoformat()}"
    if not src.is_dir():
        return []
    dst = root / str(job_id)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))
    artifacts: list[dict[str, object]] = []
    for key, artifact_type in _ARTIFACT_TYPES.items():
        path = dst / key
        if not path.is_file():
            continue
        artifacts.append(
            {
                "artifact_key": key,
                "artifact_type": artifact_type,
                "storage_uri": f"{job_id}/{key}",
                "content_checksum": _sha256(path),
                "byte_size": path.stat().st_size,
            }
        )
    return artifacts


class ExecutionRecorder:
    """把已成功的执行结果落库（workflow_steps + artifacts 目录登记）。"""

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
                artifacts = move_artifacts_to_job_dir(self._artifact_root, job_id, state.request)
            except OSError as exc:
                _LOGGER.warning("移动/登记工件失败 job=%s: %s", job_id, exc)
        with self._sf() as session:
            for step in steps:
                session.add(WorkflowStep(id=uuid.uuid4(), job_id=job_id, **step))
            for artifact in artifacts:
                session.add(Artifact(id=uuid.uuid4(), job_id=job_id, **artifact))
            session.commit()
