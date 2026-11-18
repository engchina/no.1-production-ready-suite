"""chunk の本文（引用・根拠の表示）が原本の改行・空行を残す回帰（#5419）。内容はすべて合成。"""

from rag_parser_core.registry import _text_result

from app.rag.chunking_small_to_big import build_parent_child_chunks
from app.rag.chunking_strategy import SmallToBigParams

PROCEDURE = "".join(
    f"手順{i}。合成の補足を書く。\n export V{i}=v\n /opt/bin/t{i} --run\n" for i in range(40)
)


def test_long_element_chunks_keep_line_breaks() -> None:
    """空行の無い長い手順（子 chunk の上限を超える 1 要素）でも、chunk の本文の改行が残る。"""
    extraction = _text_result(PROCEDURE.encode(), parser_profile="local_text_structure").extraction
    assert max(len(element.text) for element in extraction.elements) > 1000
    chunks = build_parent_child_chunks(extraction, params=SmallToBigParams())
    children = [chunk for chunk in chunks if "export V0=v" in chunk.text]
    assert children
    for chunk in children:
        assert "\nexport V0=v\n/opt/bin/t0 --run\n" in chunk.text
        assert "--run export" not in chunk.text
