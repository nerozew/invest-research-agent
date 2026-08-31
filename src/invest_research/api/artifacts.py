"""P04-04 工件清单与安全下载的 API DTO。

- 清单响应复用 ``application.ArtifactInfo``（保证 API 只做 HTTP 转换）。
- 下载响应为二进制流（FastAPI 用 Response 直接返回字节，不在此定义模型）。
"""

from __future__ import annotations

from invest_research.application.artifacts import ArtifactInfo

__all__ = ["ArtifactListResponse", "ArtifactDownloadResponse"]

# 清单响应 = 该 job 已登记工件的只读数组（复用 application 模型）。
ArtifactListResponse = tuple[ArtifactInfo, ...]

# 下载响应为原始字节流；路由用 fastapi.Response 返回，无需独立模型。
ArtifactDownloadResponse = bytes
