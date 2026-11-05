"""保存言語資源の欠測を現在の既定値から補わない。"""

from copy import deepcopy

import pytest

from rag_parser_core.language import FIELD_SPECS, LanguagePackError, build_pack, builtin_pack


@pytest.mark.parametrize("spec", FIELD_SPECS, ids=lambda spec: f"{spec.section}.{spec.key}")
def test_every_current_field_is_required_without_mutating_saved_input(spec):
    saved = builtin_pack().to_dict()
    del saved[spec.section][spec.key]
    before = deepcopy(saved)
    with pytest.raises(LanguagePackError):
        build_pack(saved)
    assert saved == before


def test_retired_sections_are_not_silently_removed_from_saved_pack():
    saved = builtin_pack().to_dict()
    saved["inquiry_cues"] = {"condition_terms": ["合成条件"]}
    before = deepcopy(saved)
    with pytest.raises(LanguagePackError):
        build_pack(saved)
    assert saved == before
