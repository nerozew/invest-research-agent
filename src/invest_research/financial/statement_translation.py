"""src/invest_research/financial/statement_translation.py
报表行名翻译的 LLM 兜底：通用 GAAP 对照表（statement_cn_labels）为主，
未命中的行名批量交给 LLM 一次翻译（英文→中文映射），best-effort。
数字与表格结构绝不经 LLM——本模块只翻译行名字符串。
依赖边界：仅标准库 + financial 层；LLM 通过注入的 ``llm_translate`` 可调用对象
（由 infrastructure 层用 AnnualLlmDispatcher + LLMRole.WRITER 构造）传入。
"""

from __future__ import annotations

from collections.abc import Callable

from invest_research.financial.statement_cn_labels import translate_statement_row

# infrastructure 层注入的翻译可调用：给定行名列表 → {英文行名: 中文行名}。
RowTranslateCallable = Callable[[tuple[str, ...]], dict[str, str]]


def translate_labels_with_llm(
    labels: tuple[str, ...], llm_translate: RowTranslateCallable
) -> dict[str, str]:
    """调注入的 LLM 翻译并验证；任何失败返回 {}（best-effort，回退英文）。

    只保留输入标签子集的合法字符串映射（防 LLM 编造/改数字）。
    """
    unique = tuple(dict.fromkeys(labels))
    if not unique:
        return {}
    try:
        mapping = llm_translate(unique)
    except Exception:  # noqa: BLE001 - 翻译 best-effort，失败回退英文
        return {}
    if not isinstance(mapping, dict):
        return {}
    return {
        key.strip(): str(value).strip()
        for key, value in mapping.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip() and key in unique
    }


class StatementRowTranslator:
    """报表行名翻译：通用 GAAP 对照表为主，LLM 兜底未命中行名。"""

    def __init__(self, llm_translate: RowTranslateCallable | None = None) -> None:
        self._llm_translate = llm_translate
        self._cache: dict[str, str] = {}

    def translate_many(self, labels: tuple[str, ...]) -> dict[str, str]:
        """批量翻译：对照表命中的直接取，未命中的（未在缓存）交给 LLM 一次。"""
        extra: dict[str, str] = {}
        missing: list[str] = []
        for label in dict.fromkeys(labels):
            if label in self._cache:
                extra[label] = self._cache[label]
            else:
                direct = translate_statement_row(label)
                if direct != label:
                    extra[label] = direct
                else:
                    missing.append(label)
        if missing and self._llm_translate is not None:
            translated = translate_labels_with_llm(tuple(missing), self._llm_translate)
            extra.update(translated)
            self._cache.update(translated)
        return extra

    def translate(self, label: str) -> str:
        """单个行名翻译（对照表 → 缓存 → LLM 兜底）。"""
        direct = translate_statement_row(label)
        if direct != label:
            return direct
        if label in self._cache:
            return self._cache[label]
        if self._llm_translate is not None:
            translated = translate_labels_with_llm((label,), self._llm_translate)
            if label in translated:
                self._cache[label] = translated[label]
                return translated[label]
        return label
