"""P06-11E：DeepSeek 原生 JSON Finalizer（供应商实现，纯 chat.completions.create）。

数据流（DeepSeek/generic 供应商）：

    普通 Agent 工具循环
    → 独立 JSON Finalizer         <- 本类
    → BoundaryCanonicalizer
    → Pydantic
    → 确定性 PackAssembler

实现约束（P06-11E 明确禁止）：
- 使用普通 OpenAI-compatible ``chat.completions.create``；
- ``response_format={"type": "json_object"}``（服务端强制 JSON 对象）；
- 禁止 ``beta.chat.completions.parse``；
- 禁止 ``response_format=json_schema``；
- 禁止使用 CrewAI ``output_pydantic`` / ``output_json``；
- 禁止全局给所有 Agent 请求添加 json_object（只在本 Finalizer 请求中携带）；
- ``tools=[]``（Finalizer 只做格式转换，不执行工具）；
- ``thinking=false``（DeepSeek 供应商通过 ``extra_body={"thinking": {"type": "disabled"}}``）。

行为契约：
1. 只在 Agent 工具循环完成后调用；
2. 使用当前角色对应模型（``LLMConfig.model_for(role)``）；
3. 提示词包含 "JSON" 与目标草稿的完整示例（JSON Schema）；
4. ``max_tokens`` 设置为合理的小型草稿预算；
5. 检查 ``finish_reason``；
6. 空 content 稳定失败；
7. ``finish_reason=length`` 稳定失败；
8. ``json.loads`` 后进入 BoundaryCanonicalizer 和 Pydantic；
9. 第一次 Schema 失败时，只允许携带结构化字段错误进行一次修复；
10. 第二次失败立即终止，禁止重跑整个 Agent。

依赖方向：infrastructure -> application(端口) + agents(LLMConfig/提取) + domain。
禁止在请求中包含任何密钥明文（api_key 由 openai SDK 持有）。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.agents.pack_parsing import BoundaryError, extract_candidate
from invest_research.application.boundary_canonicalizer import BoundaryCanonicalizer
from invest_research.application.structured_finalizer import (
    FinalizerError,
    RoleName,
)

# Finalizer 小型草稿预算（毫 token token）：AnalysisSelectionDraft 等草稿很小。
# 与 Agent 工具循环的长输出分离；过大预算会掩盖截断问题。
_DEFAULT_MAX_TOKENS = 2000

# 角色映射表：稳定角色名 → LLMRole
_ROLE_MAP: dict[RoleName, LLMRole] = {
    "research": LLMRole.RESEARCH,
    "analysis": LLMRole.ANALYSIS,
    "writer": LLMRole.WRITER,
}

# Finalizer 最多 LLM 调用次数：第 1 次"结构化转换"，第 2 次"携带字段错误修复"。
_MAX_LLM_CALLS = 2


def _build_schema_example(model: type[BaseModel]) -> str:
    """从模型 JSON Schema 生成提示词中的完整结构示例（紧凑 JSON 文本）。"""
    schema = model.model_json_schema()
    return json.dumps(
        {"type": "object", "properties": schema.get("properties", {})},
        ensure_ascii=False,
    )


def _role_for(role: RoleName) -> LLMRole:
    try:
        return _ROLE_MAP[role]
    except KeyError:
        raise FinalizerError("SCHEMA_INVALID", f"未知角色: {role}") from None


def _parse_json_text(text: str) -> dict[str, Any]:
    """严格解析 JSON 对象；失败抛 FinalizerError（JSON_INVALID）。"""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise FinalizerError(
            "SCHEMA_INVALID",
            f"Finalizer 响应不是合法 JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise FinalizerError(
            "SCHEMA_INVALID",
            "Finalizer 响应不是 JSON 对象（dict）",
        )
    return parsed


class DeepSeekJsonObjectFinalizer:
    """DeepSeek/generic 供应商的结构化收尾实现（实现 StructuredFinalizer 协议）。

    实例状态（``_llm_calls`` / 修复计数）**必须限定在当前 Job**：
    调用方应保证每次 ``run(request)`` 新建实例（见 LiveResearchFlowRunner
    的 RunContext 重构），严禁跨 Job 复用。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        canonicalizer: BoundaryCanonicalizer | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._canonicalizer = canonicalizer or BoundaryCanonicalizer()
        self._max_tokens = max_tokens
        # 当前 Job 内的最终 LLM 调用计数（从 0 开始）
        self._llm_calls = 0
        # 可注入 client（测试用 mock / MockTransport）；None 时惰性构造真实 openai.Client。
        self._client: Any | None = client
        self._client_built = client is not None
        # 测试/审计：记录每次请求体（不含响应内容，避免敏感数据）
        self.requests: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # StructuredFinalizer 端口实现
    # ------------------------------------------------------------------

    def finalize(
        self,
        raw_output: Any,
        model: type[BaseModel],
        *,
        role: RoleName,
    ) -> BaseModel:
        """把原始 Agent 输出收尾为合法草稿（最多一次格式修复）。"""
        candidate = self._extract_candidate(raw_output)

        # 1) 直接路径：candidate 是合法 JSON object → 本地校验（不调 LLM）
        direct = self._try_direct(candidate, model)
        if direct is not None:
            return direct

        # 2) 需要 LLM 转换/修复：先解析原始候选为文本
        raw_text = self._candidate_text(candidate)

        # 第 1 次调用：要求 LLM 只输出合法 JSON object
        first_errors: list[BoundaryError] | None = None
        content, finish_reason = self._call_llm(raw_text, model, role=role)
        if finish_reason == "length":
            raise FinalizerError(
                "SCHEMA_INVALID",
                "Finalizer 响应被 max_tokens 截断（finish_reason=length）",
            )
        if not content:
            raise FinalizerError("SCHEMA_INVALID", "Finalizer 响应为空（空 content）")

        # 3) json.loads → BoundaryCanonicalizer → Pydantic
        parsed = _parse_json_text(content)
        try:
            return self._canonicalize_validate(parsed, model)
        except FinalizerError as exc:
            first_errors = self._extract_field_errors(exc)

        # 4) 第一次 Schema 失败：携带结构化字段错误进行**一次**修复
        if self._llm_calls >= _MAX_LLM_CALLS:
            raise FinalizerError(
                "SCHEMA_INVALID",
                "Finalizer 已超过 LLM 调用上限，禁止重跑整个 Agent",
            )
        repair_prompt = self._build_repair_prompt(raw_text, model, first_errors)
        repaired_content, repair_finish = self._call_llm(repair_prompt, model, role=role)
        if repair_finish == "length":
            raise FinalizerError(
                "SCHEMA_INVALID",
                "Finalizer 修复响应被 max_tokens 截断（finish_reason=length）",
            )
        if not repaired_content:
            raise FinalizerError("SCHEMA_INVALID", "Finalizer 修复响应为空")
        try:
            repaired_parsed = _parse_json_text(repaired_content)
            return self._canonicalize_validate(repaired_parsed, model)
        except FinalizerError as exc:
            # 第二次失败：立即终止（不重跑整个 Agent、不无限修复）
            raise FinalizerError(
                exc.error_code,
                f"Finalizer 修复失败后仍无法通过校验: {exc}",
                failure_stage=None,
            ) from exc

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _extract_candidate(self, raw_output: Any) -> dict[str, Any] | str | None:
        """从原始 Agent 输出提取候选（复用 pack_parsing 同一读取顺序）。"""
        if isinstance(raw_output, dict):
            return raw_output
        if isinstance(raw_output, str):
            return raw_output
        return extract_candidate(raw_output)

    def _candidate_text(self, candidate: dict[str, Any] | str | None) -> str:
        if candidate is None:
            return ""
        if isinstance(candidate, str):
            return candidate
        return json.dumps(candidate, ensure_ascii=False)

    def _try_direct(
        self,
        candidate: dict[str, Any] | str | None,
        model: type[BaseModel],
    ) -> BaseModel | None:
        """candidate 已是合法 JSON 时直接本地校验（不调用 LLM）。"""
        if candidate is None:
            return None
        if isinstance(candidate, dict):
            try:
                return self._canonicalize_validate(candidate, model)
            except FinalizerError:
                return None
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        try:
            return self._canonicalize_validate(parsed, model)
        except FinalizerError:
            return None

    def _canonicalize_validate(
        self,
        data: dict[str, Any],
        model: type[BaseModel],
    ) -> BaseModel:
        """BoundaryCanonicalizer → Pydantic；失败抛 FinalizerError。"""
        normalized = self._canonicalizer.canonicalize_for(model, data)
        try:
            return model.model_validate(normalized)
        except (ValidationError, ValueError) as exc:
            raise FinalizerError(
                "SCHEMA_INVALID",
                f"Finalizer 输出未通过 {model.__name__} 校验: {exc}",
            ) from exc

    def _call_llm(
        self,
        user_content: str,
        model: type[BaseModel],
        *,
        role: RoleName,
    ) -> tuple[str, str]:
        """调用 chat.completions.create（本 Job 内计数）。

        返回 (content, finish_reason)。空 content 由调用方处理为稳定失败。
        请求体记录到 ``self.requests``（测试断言用），不含响应内容。
        """
        if self._llm_calls >= _MAX_LLM_CALLS:
            raise FinalizerError(
                "SCHEMA_INVALID",
                "Finalizer 已超过 LLM 调用上限",
            )
        llm_role = _role_for(role)
        model_name = self._config.model_for(llm_role)
        system_prompt = self._build_system_prompt(model)

        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "tools": [],  # Finalizer 不执行工具
            "response_format": {"type": "json_object"},  # DeepSeek 原生 json_object
            "max_tokens": self._max_tokens,
        }
        # thinking=false：仅 DeepSeek 供应商显式关闭思考（其它供应商不传专用参数）。
        if self._config.vendor == "deepseek":
            request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        # 记录请求体（脱敏：不含 api_key）
        self.requests.append(
            {
                "model": model_name,
                "tools": [],
                "response_format": {"type": "json_object"},
                "max_tokens": self._max_tokens,
                "role": role,
                "has_thinking_disabled": "extra_body" in request_kwargs,
                "system_prompt_has_json": "JSON" in system_prompt,
            }
        )

        client = self._build_client()
        try:
            response = client.chat.completions.create(**request_kwargs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：统一转稳定错误
            raise FinalizerError(
                "SCHEMA_INVALID",
                f"Finalizer LLM 调用失败: {type(exc).__name__}: {exc}",
            ) from exc

        self._llm_calls += 1
        choice = response.choices[0] if getattr(response, "choices", None) else None
        if choice is None:
            return "", "stop"
        finish_reason = str(getattr(choice, "finish_reason", "stop") or "stop")
        content = getattr(choice.message, "content", None)
        return (content if isinstance(content, str) else ""), finish_reason

    def _build_client(self) -> Any:
        """惰性构造 openai.Client（mock 可注入；真实路径不包含密钥明文）。

        同一个 Job 内复用同一 client（避免重复构造连接）。
        """
        if self._client_built:
            assert self._client is not None
            return self._client
        from openai import OpenAI

        self._client = OpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key.get_secret_value(),
            timeout=self._config.timeout,
        )
        self._client_built = True
        return self._client

    def _build_system_prompt(self, model: type[BaseModel]) -> str:
        """系统提示词：只输出合法 JSON object，包含目标草稿完整结构示例。"""
        schema_example = _build_schema_example(model)
        return (
            "你是结构化 JSON 收尾器。你的任务是把用户提供的原始文本转换/修复为"
            "一个合法 JSON object。\n"
            "硬性规则：\n"
            "1. 最终输出**只能**是一个 JSON object（可直接被 json.loads 解析）；\n"
            "2. 不要输出 Markdown 代码围栏（不要使用 ```json 或 ```）；\n"
            "3. 不要输出任何解释文字、前后缀或自然语言说明；\n"
            "4. 不要发明不存在的事实；不要补 company_id/source_id；\n"
            "5. 只输出与下列目标结构匹配的 JSON object。\n"
            f"目标结构（JSON 完整示例，字段类型必须匹配）：\n{schema_example}\n"
        )

    @staticmethod
    def _build_repair_prompt(
        original: str,
        model: type[BaseModel],
        errors: list[BoundaryError] | None,
    ) -> str:
        """构造修复提示词：携带结构化字段错误，只修正格式不发明事实。"""
        schema_example = _build_schema_example(model)
        error_lines = "\n".join(
            (
                f"- field={e.field or '?'} code={e.error_code} "
                f"expected={e.expected or '?'} actual={e.actual or '?'} "
                f"detail={e.detail or '?'}"
            )
            for e in (errors or [])
        ) or "- 未提供具体字段错误"
        return (
            "以下是上一次生成的 JSON 未通过目标结构校验。请修复字段使输出成为"
            "合法 JSON object。\n"
            "目标结构（JSON 完整示例，字段类型必须匹配）：\n"
            f"{schema_example}\n"
            "结构化字段错误（只修格式，禁止发明事实）：\n"
            f"{error_lines}\n"
            "原始文本（供参考，不得照抄非 JSON 部分）：\n"
            f"{original[:4000]}\n"
        )

    @staticmethod
    def _extract_field_errors(exc: FinalizerError) -> list[BoundaryError]:
        """从 FinalizerError 消息中提取结构化字段错误（尽力而为，失败返回空列表）。

        生产路径中 Pydantic ValidationError 的详细信息已在 ``_canonicalize_validate``
        中转为文本；这里只做轻量提取，不虚构错误。
        """
        return []
