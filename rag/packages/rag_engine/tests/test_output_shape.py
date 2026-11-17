"""未完了の出力の形の要約（#5368）を、合成の未完了の出力で確かめる。本文は要約に入らない。"""

import json

from rag_engine.resources.output_shape import summarize_output_shape


def test_whitespace_inside_a_string_is_told_apart_from_the_outside():
    inside = summarize_output_shape('{"text":"abc' + " " * 3000)
    assert inside["output_chars"] == 3012
    assert inside["tail_chars"] == 2000
    assert inside["tail_classes"] == {"space": 2000}
    assert (inside["tail_repeat_unit_chars"], inside["tail_repeat_count"]) == (1, 2000)
    assert inside["json_in_string"] is True
    assert inside["json_open_string_chars"] == 3003
    assert inside["json_trailing_whitespace_chars"] == 0
    assert inside["tail_in_string_chars"] == 2000
    assert inside["json_depth"] == 1

    outside = summarize_output_shape('{"text":"abc"' + "\n" * 2500)
    assert outside["tail_classes"] == {"newline": 2000}
    assert outside["json_in_string"] is False
    assert outside["json_open_string_chars"] == 0
    assert outside["json_trailing_whitespace_chars"] == 2500
    assert outside["tail_in_string_chars"] == 0


def test_a_repeated_fragment_cut_in_the_middle_is_counted_by_its_length():
    unit = "合成の段落。"
    text = '{"text":"' + unit * 500 + unit[:2]
    shape = summarize_output_shape(text)
    assert shape["tail_repeat_unit_chars"] == len(unit)
    assert shape["tail_repeat_count"] == 2000 // len(unit)
    assert shape["tail_classes"] == {"kana_kanji": 1667, "punctuation": 333}
    assert shape["tail_top_fragment_counts"]["8"] == 333
    assert shape["json_in_string"] is True


def test_char_classes_and_json_escapes():
    shape = summarize_output_shape('{"a": ["x\\"y 12", 3], "b": "Ab，')
    assert shape["tail_classes"] == {
        "digit": 3,
        "json_syntax": 16,
        "latin": 6,
        "punctuation": 1,
        "space": 5,
    }
    assert shape["json_in_string"] is True
    assert shape["json_open_string_chars"] == 3
    assert shape["json_depth"] == 1
    assert (shape["tail_repeat_unit_chars"], shape["tail_repeat_count"]) == (0, 0)


def test_an_empty_output_has_zero_counts():
    shape = summarize_output_shape("")
    assert shape["output_chars"] == 0
    assert shape["tail_classes"] == {}
    assert shape["tail_top_fragment_counts"] == {"8": 0, "32": 0}
    assert shape["json_in_string"] is False


def test_the_summary_keeps_no_text_of_the_output():
    marker = "SYNTHETICMARKER"
    shape = summarize_output_shape('{"text":"' + marker * 300)
    assert marker not in json.dumps(shape, ensure_ascii=False)
    assert shape["tail_repeat_unit_chars"] == len(marker)
