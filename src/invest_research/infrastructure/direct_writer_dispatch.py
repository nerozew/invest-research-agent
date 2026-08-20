"""P06-11I：DeepSeek/generic Writer 无工具单轮直接调度实现。

背景（根因）：
- 旧 Writer 在 CrewAI 工具循环中反复调用 WriterContextReader，DeepSeek 把 token
  消耗在 tool_calls 上，final answer 过短 → REPORT_INVALID；
- 本实现用**一次普通 openai-compatible chat.completions.create** 直接输出 Markdown，
  不创建 Agent/Crew 工具循环。

实现约束（本任务明确禁止）：
- 不传 ``tools`` / ``tool_choice`` / ``available_functions``；
- 不触发 ``beta.chat.completions.parse``（不使用 response_model / json_schema）；
- 只读取普通 ``response.choices[0].message.content``（DeepSeek 普通响应正文）；
- 不创建 WriterContextReader / Agent / Crew / max_iter 循环；
- 重试由调用方编排（flow_wiring），本实现只提供单次调用 + 请求体审计。

可观测性（只记录低基数元数据，绝不记录 prompt / 报告正文 / API Key）：
- ``self.requests``：每次请求的脱敏结构（model / 消息条数与长度 / 是否带
  error_summary / finish_reason），供测试断言"请求完全不包含 tools"；
- Jaeger span 与 Prometheus 指标由 flow_wiring 在调用边界记录。

依赖方向：infrastructure -> agents（LLMConfig/LLMRole）+ application（端口/上下文）。
"""

from __future__ import annotations

import time
from typing import Any

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.application.writer_context_builder import BuiltWriterContext
from invest_research.application.writer_direct_dispatch import (
    WriterDispatchError,
    WriterDispatchResult,
)

# Writer 报告正文预算（token）。报告比结构化草稿长得多；过小会掩盖截断。
_DEFAULT_MAX_TOKENS = 4096

# 系统提示词（固定、稳定，便于测试断言；不含任何公司/密钥信息）。
_SYSTEM_PROMPT = (
    "你是专业研究报告编辑。根据用户提供的公司身份、财务事实、可信来源与引用注册表，"
    "直接输出一份完整的 Markdown 中文研究报告正文。\n"
    "硬性要求：\n"
    "1. 只输出 Markdown 正文本身（章节用 ## 或 ###），不要 JSON、不要代码围栏、"
    "不要任何解释文字或前后缀；\n"
    "2. 只能引用用户消息中 citation keys 列出的合法 key，格式 [src_<hash>] 或 "
    "[fr_<hash>]，禁止自行生成或猜测 key；\n"
    "3. 必须包含用户消息中列出的必需章节；\n"
    "4. 禁止给出买入/卖出建议、目标价、持仓比例或确定性收益承诺；必须保留非投资建议声明；\n"
    "5. 只使用用户消息中提供的财务事实与来源，绝不引入新事实；\n"
    "6. 数据不完整（partial/unavailable）时在数据限制章节如实说明，不得编造。"
)


class DirectLlmWriterDispatch:
    """无工具 Writer 单次调用实现（实现 WriterDirectDispatch 协议）。

    每次调用只执行一次 ``chat.completions.create``；重试编排与失败判定由调用方
    负责（flow_wiring 用 ReportDraftAssembler 判断过短/缺章节）。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._max_tokens = int(max_tokens)
        # 可注入 client（测试用 mock / MockTransport）；None 时惰性构造真实 openai.Client。
        self._client: Any | None = client
        self._client_built = client is not None
        # 审计：每次请求的脱敏结构（不含 prompt 正文 / 报告正文 / API Key）。
        self.requests: list[dict[str, Any]] = []

    def dispatch(
        self,
        request: Any,
        context: BuiltWriterContext,
        *,
        error_summary: str | None = None,
    ) -> WriterDispatchResult:
        """执行一次无工具 Writer 调用（普通 chat.completions.create）。

        - ``request``：ResearchRequest——仅用于低基数观测，不进入 prompt；
        - ``context``：确定性紧凑上下文（已构建）；
        - ``error_summary``：第二次调用携带的结构化错误摘要（可为 None）。
        """
        role_cfg = self._config.config_for(LLMRole.WRITER)
        user_content = context.text
        if error_summary:
            user_content = (
                f"{context.text}\n\n"
                "# 上一次输出失败，请修复后重新输出完整 Markdown 报告\n"
                f"失败原因：{error_summary}"
            )

        request_kwargs: dict[str, Any] = {
            "model": role_cfg.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": self._max_tokens,
        }
        # P06-11G：thinking 按 Writer 角色配置（deepseek=thinking.type；
        # 与 build_real_llm / DeepSeekJsonObjectFinalizer 同一决策源）。
        if role_cfg.enable_thinking is not None and role_cfg.vendor == "deepseek":
            request_kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if role_cfg.enable_thinking else "disabled"}
            }

        # 审计（脱敏）：记录消息条数与每段长度，不记录正文。
        self.requests.append(
            {
                "model": role_cfg.model,
                "tools": None,  # 绝不传 tools
                "tool_choice": None,  # 绝不传 tool_choice
                "available_functions": None,  # 绝不传 available_functions
                "messages": [
                    {"role": "system", "chars": len(_SYSTEM_PROMPT)},
                    {"role": "user", "chars": len(user_content)},
                ],
                "max_tokens": self._max_tokens,
                "has_error_summary": bool(error_summary),
            }
        )

        client = self._build_client()
        started = time.monotonic()
        try:
            response = client.chat.completions.create(**request_kwargs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：统一转稳定错误
            raise WriterDispatchError(
                "WRITER_LLM_CALL_FAILED",
                f"Direct Writer LLM 调用失败: {type(exc).__name__}: {exc}",
            ) from exc
        duration_s = time.monotonic() - started

        choice = response.choices[0] if getattr(response, "choices", None) else None
        if choice is None:
            return WriterDispatchResult(markdown="", finish_reason="stop", duration_s=duration_s)
        finish_reason = str(getattr(choice, "finish_reason", "stop") or "stop")
        content = getattr(choice.message, "content", None)
        content = content if isinstance(content, str) else ""

        input_tokens: int | None = None
        output_tokens: int | None = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = _int_or_none(getattr(usage, "prompt_tokens", None))
            output_tokens = _int_or_none(getattr(usage, "completion_tokens", None))
        # 审计：记录 finish_reason 与正文长度（不记录正文）。
        self.requests[-1]["finish_reason"] = finish_reason
        self.requests[-1]["output_chars"] = len(content)
        return WriterDispatchResult(
            markdown=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=duration_s,
        )

    def _build_client(self) -> Any:
        """惰性构造 openai.Client（mock 可注入；真实路径不包含密钥明文）。"""
        if self._client_built:
            assert self._client is not None
            return self._client
        from openai import OpenAI

        role_cfg = self._config.config_for(LLMRole.WRITER)
        self._client = OpenAI(
            base_url=role_cfg.base_url,
            api_key=role_cfg.api_key.get_secret_value(),
            timeout=role_cfg.timeout,
        )
        self._client_built = True
        return self._client


def _int_or_none(value: Any) -> int | None:
    """把 usage 字段转为非负 int（None/非法返回 None）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


__all__ = ["DirectLlmWriterDispatch"]
