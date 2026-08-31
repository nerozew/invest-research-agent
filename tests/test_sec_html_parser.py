"""P02-09 SEC HTML 解析器测试（纯函数，标准库，不依赖网络）。"""

from __future__ import annotations

from invest_research.tools.sec_html_parser import parse_html


def test_extracts_visible_text_and_headings() -> None:
    """提取标题（h1）与正文（p），跳过 script/style。"""
    html = (
        "<html><head><script>var x=1;</script></head>"
        "<body><h1>Item 1</h1><p>Business overview.</p></body></html>"
    )
    doc = parse_html(html)

    assert [b.text for b in doc.headings] == ["Item 1"]
    assert [b.text for b in doc.blocks] == ["Item 1", "Business overview."]


def test_table_text_preserved() -> None:
    """表格内文本（td）被保留并规范化空白。"""
    html = "<table><tr><td>Revenues</td><td> 100 </td></tr></table>"
    doc = parse_html(html)

    texts = [b.text for b in doc.blocks]
    assert "Revenues" in texts
    assert "100" in texts


def test_whitespace_normalized() -> None:
    """连续空白、换行被归一为单个空格。"""
    doc = parse_html("<p>a   \n  b</p>")
    assert [b.text for b in doc.blocks] == ["a b"]


def test_empty_html_yields_no_blocks() -> None:
    doc = parse_html("")
    assert doc.blocks == ()
    assert doc.headings == ()
