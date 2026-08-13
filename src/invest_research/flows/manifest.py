"""P03-14 发布与 RunManifest（步骤 07，确定性）。

对齐 docs/04 §10「95% 成功率评估报告模板」与 §2 步骤表 07：
- 只有 quality_report.all_passed 才可发布；
- manifest 记录：代码/依赖指纹（实现层取 prompt 与 pack 的 sha256、
  模型名；真实 git commit / uv.lock hash 由 P04-05 运行环境注入）、
  三份 pack 的 checksum、公司/CIK/as-of、耗时、最终状态。

本模块为纯函数，不依赖 CrewAI；可完全离线单测。
"""

from __future__ import annotations

import hashlib
import time
from datetime import date

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.flows.state import ResearchFlowState
from invest_research.prompts.loader import PromptName, prompt_sha256

# manifest schema 版本（配合 schema 变化时可追溯）
MANIFEST_VERSION = "run_manifest_v1"


def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack_checksum(pack: object | None) -> str | None:
    """给 pack 计算内容指纹（可复现校验：同 pack 必同 hash）。"""
    if pack is None:
        return None
    # BaseModel 提供 model_dump_json；边界用 str 兜底
    dump = getattr(pack, "model_dump_json", lambda: str(pack))()
    return _sha256_of(dump)


def build_run_manifest(
    state: ResearchFlowState,
    config: LLMConfig,
    started_at: float | None = None,
) -> dict[str, object]:
    """生成 RunManifest（仅在质量门禁通过时发布；否则返回 rejected 标记）。

    - ``config``：用于记录三个角色的模型名（供应商无关，仅存名称）；
    - ``started_at``：任务开始时间戳（调用方传入；默认用当前时间）。
    """
    if state.quality_report is None or not state.quality_report.all_passed:
        return {
            "version": MANIFEST_VERSION,
            "status": "rejected",
            "reason": (
                state.quality_report.recommendation if state.quality_report else "no_quality_report"
            ),
        }

    # 公司/CIK/as-of
    company_identity = state.company_identity
    request = state.request
    as_of: date | None = request.as_of_date if request else None
    cik: str | None = company_identity.cik if company_identity else None

    # 模型名（供应商无关：只记录角色→模型名映射）
    models: dict[str, str] = {
        "research": config.model_for(LLMRole.RESEARCH),
        "analysis": config.model_for(LLMRole.ANALYSIS),
        "writer": config.model_for(LLMRole.WRITER),
    }

    # 提示词版本与指纹（manifest 可判断"哪版说明书"）
    prompts: dict[str, str] = {
        "research_version": PromptName.RESEARCH.value,
        "analysis_version": PromptName.ANALYSIS.value,
        "writer_version": PromptName.WRITER.value,
        "research_sha256": prompt_sha256(PromptName.RESEARCH),
        "analysis_sha256": prompt_sha256(PromptName.ANALYSIS),
        "writer_sha256": prompt_sha256(PromptName.WRITER),
    }

    # 三份 pack 的 checksum（可复现校验）
    packs: dict[str, str | None] = {
        "research_pack_sha256": _pack_checksum(state.research_pack),
        "analysis_pack_sha256": _pack_checksum(state.analysis_pack),
        "report_draft_sha256": _pack_checksum(state.report_draft),
        "quality_report_sha256": _pack_checksum(state.quality_report),
    }

    ended_at = time.time()
    started = started_at if started_at is not None else ended_at
    duration_ms = int((ended_at - started) * 1000)

    return {
        "version": MANIFEST_VERSION,
        "status": "published",
        "company": cik,
        "as_of_date": as_of.isoformat() if as_of else None,
        "models": models,
        "prompts": prompts,
        "packs": packs,
        "duration_ms": duration_ms,
        "started_at": started,
        "ended_at": ended_at,
    }
