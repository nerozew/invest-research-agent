"""Streamlit 前端模块（P04-UI 系列）。

Streamlit 只是 FastAPI 的 HTTP 客户端（见 `docs/02-ARCHITECTURE.md §11`）。
本模块提供 typed API client（``models.py`` / ``errors.py`` / ``client.py``），
UI 页面位于仓库根目录 ``frontend/``，只调用本 client，不直接访问
数据库 / Redis / CrewAI Flow，也不读取服务器工件路径。

依赖方向：frontend -> domain（仅使用纯 Python 的 pydantic 与状态枚举），
禁止 import api/application/infrastructure。
"""
