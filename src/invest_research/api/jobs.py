"""P04-02 创建 + P04-03 查询投研任务的 API DTO。

- 创建请求体重用 ``domain.ResearchRequest``（自带校验：空公司名、未来日期、
  非法语言、空表单），FastAPI 对非法请求自动返回 422；成功返回 202 + job_id。
- 查询响应复用 ``application.JobSnapshot``/``StepSnapshot``（FR-013：状态、步骤、
  错误、耗时），路由只做 HTTP 语义转换。
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from invest_research.application.jobs import JobSnapshot
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus

__all__ = [
    "CreateResearchJobRequest",
    "CreateResearchJobResponse",
    "GetResearchJobResponse",
]

# 请求体重用领域模型：字段即 FR-001 的输入（公司/ticker、as_of_date、语言、表单）。
CreateResearchJobRequest = ResearchRequest


class CreateResearchJobResponse(BaseModel):
    """创建成功响应（HTTP 202 Accepted）。"""

    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    status: JobStatus


# 查询响应直接复用 application 层的 JobSnapshot/StepSnapshot，
# 保证"API 只做 HTTP 转换、业务模型只在 application 定义"。
GetResearchJobResponse = JobSnapshot
