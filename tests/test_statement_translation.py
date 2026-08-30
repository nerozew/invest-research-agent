from collections.abc import Callable

from invest_research.financial.statement_translation import (
    StatementRowTranslator,
    translate_labels_with_llm,
)


def _fake_llm(
    mapping: dict[str, str],
) -> Callable[[tuple[str, ...]], dict[str, str]]:
    return lambda labels: {k: mapping[k] for k in labels if k in mapping}


def test_translate_labels_with_llm_returns_valid_mapping() -> None:
    llm_translate = _fake_llm({"SomeOddRow": "某生僻行", "AnotherRow": "另一行"})
    result = translate_labels_with_llm(("SomeOddRow", "AnotherRow"), llm_translate)
    assert result == {"SomeOddRow": "某生僻行", "AnotherRow": "另一行"}


def test_translate_labels_with_llm_fails_gracefully() -> None:
    # llm_translate 抛异常 / 返回非 dict / 返回输入外标签 → 全部安全处理
    def bad(labels):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm down")

    assert translate_labels_with_llm(("X",), bad) == {}

    def bad_shape(labels):  # type: ignore[no-untyped-def]
        return "not a dict"

    assert translate_labels_with_llm(("X",), bad_shape) == {}


def test_translate_labels_with_llm_drops_out_of_subset_keys() -> None:
    # LLM 返回输入外的标签 → 必须被丢弃（防编造 key）。
    def fabricating_llm(labels: tuple[str, ...]) -> dict[str, str]:
        return {"X": "x", "Y": "y"}

    result = translate_labels_with_llm(("X",), fabricating_llm)
    assert result == {"X": "x"}


def test_translator_uses_lookup_table_first_and_llm_for_missing() -> None:
    translator = StatementRowTranslator(_fake_llm({"MysteryRow": "神秘行"}))
    assert translator.translate("Net income") == "净利润"  # 对照表命中
    assert translator.translate("MysteryRow") == "神秘行"  # 对照表未命中 → LLM 兜底
    assert translator.translate_many(("Net income", "MysteryRow")) == {
        "Net income": "净利润",  # 对照表命中不送 LLM
        "MysteryRow": "神秘行",  # 先前 translate 已由 LLM 翻译并缓存
    }


def test_translate_many_only_sends_missing_rows_to_llm() -> None:
    received: list[tuple[str, ...]] = []

    def recording_llm(labels: tuple[str, ...]) -> dict[str, str]:
        received.append(labels)
        return {"MysteryRow": "神秘行"}

    translator = StatementRowTranslator(recording_llm)
    translator.translate_many(("Net income", "MysteryRow"))
    assert received == [("MysteryRow",)]  # 对照表命中的 Net income 不送 LLM


def test_translator_without_llm_keeps_english() -> None:
    translator = StatementRowTranslator(None)
    assert translator.translate("TotallyUnknownRow") == "TotallyUnknownRow"
