"""application 层：用例与端口（P04-02）。

依赖方向：application -> domain；application 不导入 infrastructure/CrewAI/FastAPI。
持久化或外部能力通过本层定义的 Protocol（端口）注入，由 infrastructure 提供实现。
"""
