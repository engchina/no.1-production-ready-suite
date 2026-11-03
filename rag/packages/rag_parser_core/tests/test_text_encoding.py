"""UTF-8 ではないテキストの文字コードの判定（合成の文）。"""

import pytest
from charset_normalizer import from_bytes

from rag_parser_core.source_map import decode_source_text

_JAPANESE = [
    "番号,値,備考\n" + "".join(f"{i},合成の値,備考 {i}\n" for i in range(1, 300)),
    "番号,値,備考\n1,合成の値,備考\n",
    "番号,値,備考\n" + "".join(f"{i},合成値,備考{i}\n" for i in range(1, 40)),
    "氏名\n山田\n鈴木\n",
    "ｱｲｳｴｵ,ｶｷｸｹｺ\n" * 30,
    "これは文字コードの判定を確かめる合成の文章です。\n" * 50,
    "\n\n".join(f"# 見出し{i}\n\n合成の値{i}" for i in range(500)),
]


@pytest.mark.parametrize("encoding", ["cp932", "euc_jp"])
@pytest.mark.parametrize("text", _JAPANESE)
def test_japanese_is_read_as_japanese(text: str, encoding: str) -> None:
    assert decode_source_text(text.encode(encoding)) == text


def test_detector_alone_reads_this_japanese_as_another_language() -> None:
    """検出器の 1 位がこの文を誤る前提。検出器の版で変われば、優先の要否を見直す。"""
    data = _JAPANESE[1].encode("cp932")
    assert str(from_bytes(data).best()) != _JAPANESE[1]


@pytest.mark.parametrize(
    "text",
    [
        "注記①は合成の値です。㈱の記号も含みます。\n" * 20,
        "１２３ＡＢＣ，全角の値，（備考）\n" * 10,
        "－－－－－－\n■合成の表■\n※注記\n" * 5,
    ],
)
def test_japanese_with_many_symbols_is_read_when_the_detector_has_no_candidate(text: str) -> None:
    data = text.encode("cp932")
    assert from_bytes(data).best() is None
    assert decode_source_text(data) == text


@pytest.mark.parametrize(
    ("encoding", "text"),
    [
        ("gbk", "这是用于确认字符编码判定的合成文章。\n" * 40),
        ("gbk", "编号,数值,备注\n1,合成值,备注\n2,合成值,备注\n"),
        ("euc_kr", "이것은 문자 인코딩 판정을 확인하기 위한 합성 문장입니다.\n" * 40),
        ("euc_kr", "번호,값,비고\n1,합성값,비고\n2,합성값,비고\n"),
        ("big5", "這是用於確認字元編碼判定的合成文章。\n" * 40),
        ("cp1251", "Это синтетический текст для проверки кодировки.\n" * 20),
    ],
)
def test_other_languages_keep_the_detector_result(encoding: str, text: str) -> None:
    assert decode_source_text(text.encode(encoding)) == text
