"""最终报告工件发布服务（P06-07 前置修复）。

背景（集成缺口）：
- P06-01/02 的 ``ReportRenderer`` / ``MarkdownPdfRenderer`` 只在 live runner 的
  ``_persist_intermediates`` 内被调用，fake Flow 不执行该分支；
- ``ExecutionRecorder._ARTIFACT_TYPES`` 只登记 00-07 JSON，08_report.md /
  09_report.pdf 永不进 artifacts 表 → 工件 API 与前端只能看到 JSON。

本模块把「最终报告生成 + 发布」提取为 fake/live **共用**的确定性服务：
- 输入：``job_id`` + ``ResearchFlowState``（Worker 成功获得 Flow state 后调用）；
- 输出：在 ``<artifact_root>/<job_id>/`` 下写入 08_report.md / 09_report.pdf
  （原子写，overwrite=True：live 单次运行覆盖旧工件，避免 company_as_of 目录并发覆盖）；
- 返回：两条登记元数据（artifact_key / artifact_type / storage_uri /
  content_checksum / byte_size），由 ExecutionRecorder 幂等登记进 artifacts 表。

失败语义：缺少 report_draft 或 Markdown/PDF 任一渲染失败都抛
``ReportArtifactPublishError``——Worker 收到该异常会把任务标记为 failed，
**绝不允许**把"报告渲染失败"误报成"完整发布成功"。

依赖方向：reporting -> flows/state + tools/artifact_store（确定性、无 DB/网络）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.flows.state import ResearchFlowState
from invest_research.reporting.pdf import MarkdownPdfRenderer
from invest_research.reporting.renderer import ReportRenderer, build_render_input
from invest_research.tools.artifact_store import ArtifactStore

__all__ = [
    "ReportArtifactPublishError",
    "ReportArtifactPublisher",
]

# 最终报告工件文件名 → artifact_type（与 ExecutionRecorder._ARTIFACT_TYPES 对齐）
_REPORT_FILE_SPECS: tuple[tuple[str, str], ...] = (
    ("08_report.md", "final_report_markdown"),
    ("09_report.pdf", "final_report_pdf"),
)


class ReportArtifactPublishError(RuntimeError):
    """最终报告发布失败（缺少草稿 / Markdown 渲染失败 / PDF 渲染失败）。

    语义：发布失败属于 Worker 失败路径——任务不得标记为完整发布成功。
    """


class ReportArtifactPublisher:
    """把 Flow state 渲染为最终 Markdown/PDF 并发布到 ``artifacts/<job_id>/``。"""

    def __init__(self, artifact_root: str | Path) -> None:
        self._artifact_root = Path(artifact_root).resolve()

    def publish(self, job_id: uuid.UUID, state: ResearchFlowState) -> list[dict[str, object]]:
        """生成并发布 08_report.md / 09_report.pdf，返回登记元数据列表。

        幂等：overwrite=True，重复调用用本次运行内容原子替换旧文件；
        数据库登记由 ExecutionRecorder 负责（按 (job_id, artifact_key) 幂等）。
        """
        rendered = build_render_input(state)
        if rendered is None:
            raise ReportArtifactPublishError(
                "缺少 report_draft，无法渲染最终报告；任务不得标记为完整发布成功"
            )
        try:
            if (state.run_manifest or {}).get("research_mode") == ResearchMode.ANNUAL_DEEP.value:
                # 年度报告自包含（H1 + 封面 + 章节 + 数据限制 + 来源清单 + 声明），
                # 直接发布，避免再套 legacy 模板导致标题/来源/限制/声明重复。
                if state.report_draft is None:
                    raise ReportArtifactPublishError(
                        "缺少 report_draft，无法发布年度报告；任务不得标记为完整发布成功"
                    )
                report_md = state.report_draft.markdown or ""
            else:
                report_md = ReportRenderer().render(rendered)
        except Exception as exc:  # noqa: BLE001 - 渲染失败统一转发布错误
            raise ReportArtifactPublishError(
                f"Markdown 报告渲染失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not report_md or not report_md.strip():
            raise ReportArtifactPublishError("Markdown 报告渲染结果为空，无法发布")

        try:
            pdf_bytes = MarkdownPdfRenderer().render_to_bytes(report_md)
        except Exception as exc:  # noqa: BLE001 - 渲染失败统一转发布错误
            raise ReportArtifactPublishError(
                f"PDF 报告渲染失败: {type(exc).__name__}: {exc}"
            ) from exc

        job_root = self._artifact_root / str(job_id)
        store = ArtifactStore(job_root)
        md_bytes = report_md.encode("utf-8")

        published: list[dict[str, object]] = []
        for key, artifact_type in _REPORT_FILE_SPECS:
            content = md_bytes if key.endswith(".md") else pdf_bytes
            ref = store.write(key, content, overwrite=True)
            published.append(
                {
                    "artifact_key": ref.artifact_key,
                    "artifact_type": artifact_type,
                    "storage_uri": f"{job_id}/{ref.artifact_key}",
                    "content_checksum": ref.content_checksum,
                    "byte_size": ref.byte_size,
                }
            )
        return published
