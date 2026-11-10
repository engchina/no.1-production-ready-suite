"""廃止した節の上書きは読み飛ばさず、知らない項目として拒む（保存値は migration で消す。#4784）。"""

import pytest
from rag_parser_core.language import FIELD_SPECS, LanguagePackError, builtin_pack, resolve


def test_retired_inquiry_overrides_are_rejected():
    with pytest.raises(LanguagePackError) as caught:
        resolve(builtin_pack(), {"inquiry_cues": {"column_words": ["合成"]}})
    assert "inquiry_cues.column_words" in caught.value.errors
    assert not any(field.section == "inquiry_cues" for field in FIELD_SPECS)


def test_other_unknown_fields_are_still_rejected():
    with pytest.raises(LanguagePackError):
        resolve(builtin_pack(), {"question_cues": {"not_a_field": ["合成"]}})
