"""合成文書による参照解決の曖昧さの回帰。"""

from functools import partial
from typing import Any

import pytest

from app.rag.cross_references import ReferenceSpec, SectionIndex, extract_references
from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference


def test_equally_specific_targets_are_not_selected_by_document_order() -> None:
    index = SectionIndex(["第1章 > 第2節", "第2章 > 第2節"])
    assert index.resolve(ReferenceSpec("第2節", "jsection", "2")) is None


def test_scoped_cross_document_resolution_pins_version_and_rejects_ambiguity() -> None:
    from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference

    source = ReferenceDocument("source", "s1", "synthetic-source.txt", ("第1条",))
    target = ReferenceDocument("target", "t1", "合成参照文書.txt", ("第2条",))
    spec = ReferenceSpec("合成参照文書第2条", "article", "2", "合成参照文書")
    resolve = partial(resolve_scoped_reference, spec, source=source, citing_path="第1条")
    resolved = resolve(documents=(target,))
    assert resolved.status == "resolved"
    assert resolved.target is not None and resolved.target.chunk_set_id == "t1"
    assert resolve(documents=()).status == "not_found"
    other_document = ReferenceDocument("another", "a1", target.name, target.section_paths)
    assert resolve(documents=(target, other_document)).status == "ambiguous"
    other_version = ReferenceDocument(target.document_id, "t2", target.name, target.section_paths)
    assert resolve(documents=(target, other_version)).status == "version_unconfirmed"
    unknown_version = ReferenceDocument(target.document_id, None, target.name, target.section_paths)
    assert resolve(documents=(unknown_version,)).target is None


def test_resolving_many_references_parses_each_catalog_document_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import cross_references, reference_resolution

    alias_calls: list[str] = []
    index_calls: list[tuple[str, ...]] = []
    aliases = cross_references.document_name_aliases
    section_index = cross_references.SectionIndex

    def counted_aliases(value: str) -> set[str]:
        alias_calls.append(value)
        return aliases(value)

    def counted_index(paths: tuple[str, ...]) -> Any:
        index_calls.append(paths)
        return section_index(paths)

    monkeypatch.setattr(cross_references, "document_name_aliases", counted_aliases)
    monkeypatch.setattr(reference_resolution, "SectionIndex", counted_index)
    source = ReferenceDocument("source", "s1", "合成の起点.txt", ("第1条",))
    target = ReferenceDocument("target", "t1", "合成規程.txt", ("第1条", "第2条", "第3条"))
    other = ReferenceDocument("other", "o1", "合成細則.txt", ("第1条",))
    spec = extract_references("合成規程の第2条を参照する。")[0]
    results = [
        resolve_scoped_reference(
            spec, source=source, citing_path="第1条", documents=(target, other)
        )
        for _ in range(50)
    ]
    assert {result.status for result in results} == {"resolved"}
    assert {result.target.section_path for result in results if result.target} == {"第2条"}
    # 文書のファイル名の1回と、見出しになれる章節ごとの1回を超えない。
    assert len(alias_calls) <= 2 + len(target.section_paths) + len(other.section_paths)
    assert index_calls == [target.section_paths]
