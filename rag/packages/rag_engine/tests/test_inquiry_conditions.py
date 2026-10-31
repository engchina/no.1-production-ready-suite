"""廃止した規則の設定が旧プロファイルの読込を壊さない。"""

import pytest
from rag_parser_core.language import FIELD_SPECS, LanguagePackError, builtin_pack, resolve


def test_retired_inquiry_overrides_are_ignored():
    base = builtin_pack()
    pack = resolve(base, {"inquiry_cues": {"column_words": ["合成"], "external_context_terms": ["合成"]}})
    assert pack.hash == base.hash
    assert not any(field.section == "inquiry_cues" for field in FIELD_SPECS)


def test_other_unknown_fields_are_still_rejected():
    with pytest.raises(LanguagePackError):
        resolve(builtin_pack(), {"question_cues": {"not_a_field": ["合成"]}})
