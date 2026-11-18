"""書き込みは全項目を要求し、保存済みの読み取り(fill_missing)だけ欠けた項目を組み込みで補う。"""

from copy import deepcopy

import pytest

from rag_parser_core.language import (
    FIELD_SPECS,
    LanguagePackError,
    _digest,
    build_pack,
    builtin_pack,
)


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


@pytest.mark.parametrize("ingest", [True, False])
def test_fill_missing_uses_builtin_value_and_hashes_only_saved_fields(ingest):
    """項目を足す前に保存したもの: 組み込みの値で補い、hash は保存していた項目だけ(#5476)。"""
    added = next(spec for spec in FIELD_SPECS if spec.ingest is ingest)
    builtin = builtin_pack()
    saved = builtin.to_dict()
    del saved[added.section][added.key]
    before = deepcopy(saved)
    pack = build_pack(saved, fill_missing=True)
    assert pack.value(added.section, added.key) == builtin.value(added.section, added.key)
    given = tuple(spec for spec in FIELD_SPECS if spec != added)
    assert pack.hash == _digest(builtin.locale, builtin.sections, given)
    assert pack.ingest_hash == _digest(
        builtin.locale, builtin.sections, tuple(spec for spec in given if spec.ingest)
    )
    # 取込の項目でない項目を足しても ingest_hash は変わらない。
    assert (pack.ingest_hash == builtin.ingest_hash) is not ingest
    assert saved == before


def test_fill_missing_still_rejects_invalid_and_unknown_values():
    spec = next(item for item in FIELD_SPECS if item.kind.value == "pairs")
    saved = builtin_pack().to_dict()
    saved[spec.section][spec.key] = ["合成の誤った組"]
    saved["answer_labels"]["unknown_key"] = "合成"
    with pytest.raises(LanguagePackError) as raised:
        build_pack(saved, fill_missing=True)
    assert set(raised.value.errors) == {f"{spec.section}.{spec.key}", "answer_labels.unknown_key"}


def test_complete_pack_hash_is_unchanged_by_fill_missing():
    saved = builtin_pack().to_dict()
    assert build_pack(saved, fill_missing=True).hash == builtin_pack().hash
