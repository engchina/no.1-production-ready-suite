"""SQL の範囲と、永続化前の原文照合を接続せずに確認する。"""

from typing import Any, cast

import pytest

from app.clients.wiki_store import WikiSnapshot, WikiStore, wiki_scope
from app.rag.wiki_models import WikiContext, WikiPage, WikiUnit, fingerprint
from tests.test_wiki_compiler import units


def test_explicit_version_is_not_replaced_with_current_version() -> None:
    sql, binds = wiki_scope({"knowledge_base_id": "kb-a", "chunk_set_id": "requested-version"})
    assert "requested-version" in binds.values()
    assert "wiki_current_set.is_active=1" in sql
    assert "wiki_current_set.status='INDEXED'" in sql


@pytest.mark.asyncio
async def test_changed_body_is_not_returned_as_the_cached_original() -> None:
    unit = units()[0]

    class Oracle:
        async def _fetch_all(self, sql: str, binds: Any) -> Any:
            assert "wiki_build_enabled" in sql and "kb-a" in binds.values()
            return [
                {
                    "chunk_id": unit.source_chunk_ids[0],
                    "document_id": unit.document_id,
                    "chunk_set_id": unit.chunk_set_id,
                    "chunk_text": unit.text + " 変更。",
                }
            ]

    page = WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    assert not await WikiStore(cast(Any, Oracle())).read_sources(
        [page], {"knowledge_base_id": "kb-a"}
    )


@pytest.mark.asyncio
async def test_persistence_rejects_a_page_that_changed_the_authoritative_unit() -> None:
    unit = units()[0]
    page = WikiPage(
        unit=unit.model_copy(update={"text": "生成された別本文"}),
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    with pytest.raises(ValueError, match="原文と構築対象"):
        await WikiStore(cast(Any, object())).checkpoint(
            WikiSnapshot("document-a", "source-a", (unit,)), "lease-a", [page]
        )


@pytest.mark.asyncio
async def test_text_score_is_read_before_hydration_and_keeps_its_order() -> None:
    pages = [
        WikiPage(
            unit=u,
            navigation=WikiContext(
                unit_id=u.unit_id,
                context="案内",
                possible_questions=[],
                dependencies=[],
                unresolved=[],
            ),
            input_fingerprint="input-a",
        )
        for u in units()[:2]
    ]

    class Oracle:
        def __init__(self) -> None:
            self.calls = 0

        async def _fetch_all(self, sql: str, binds: Any) -> Any:
            self.calls += 1
            assert "kb-a" in binds.values() and "wiki_build_enabled" in sql
            if self.calls == 1:
                assert "SCORE(1)" in sql and "CONTAINS(p.search_text" in sql
                assert "page_json" not in sql and "WITH text_hits" not in sql
                return [{"unit_id": p.unit.unit_id, "text_score": 10} for p in reversed(pages)]
            assert "SCORE(" not in sql and "p.unit_id IN" in sql
            return [{"page_json": p.model_dump()} for p in pages]

    oracle = Oracle()
    hits = await WikiStore(cast(Any, oracle)).text_pages(
        "synthetic", {"knowledge_base_id": "kb-a"}, compiler="compiler-a", limit=2
    )
    assert oracle.calls == 2
    assert [p.unit.unit_id for p in hits] == [p.unit.unit_id for p in reversed(pages)]


def _originals() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": f"source-{index}",
            "document_id": "document-a",
            "chunk_set_id": "version-a",
            "chunk_index": index,
            "chunk_text": text,
            "file_name": "synthetic.txt",
            "metadata_json": {
                "parent_chunk_id": "virtual-parent",
                "section_path": ["合成資料", "操作"],
                "parent_text": "原文にない写しは構築へ入れない。",
            },
        }
        for index, text in enumerate(
            ["前提を確認する。", "手順を実行する。", "警告と完了条件を確認する。"]
        )
    ]


class _OriginalStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def _fetch_all(self, sql: str, binds: Any) -> Any:
        return self.rows


@pytest.mark.asyncio
async def test_virtual_parent_groups_originals_without_using_metadata_body() -> None:
    rows = _originals()
    source = await WikiStore(cast(Any, _OriginalStore(rows))).source_units("document-a")
    assert source is not None and len(source.units) == 1
    unit = source.units[0]
    assert unit.source_chunk_ids == [row["chunk_id"] for row in rows]
    assert unit.text == "\n\n".join(row["chunk_text"] for row in rows)
    assert unit.source_chunk_hashes == {
        row["chunk_id"]: fingerprint(row["chunk_text"]) for row in rows
    }
    assert unit.section_path == ["合成資料", "操作"]


@pytest.mark.asyncio
async def test_real_parent_is_used_and_virtual_parent_ids_do_not_cross_versions() -> None:
    rows = _originals()
    rows.append(
        {
            **rows[0],
            "chunk_id": "virtual-parent",
            "chunk_text": "保存された親の原文。",
            "metadata_json": {},
        }
    )
    rows.append({**rows[1], "chunk_id": "other-version", "chunk_set_id": "version-b"})
    source = await WikiStore(cast(Any, _OriginalStore(rows))).source_units("document-a")
    assert source is not None and len(source.units) == 2
    assert source.units[0].source_chunk_ids == ["virtual-parent"]
    assert source.units[1].source_chunk_ids == ["other-version"]
    assert source.units[0].unit_id != source.units[1].unit_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "missing", "body", "document", "version"])
async def test_grouped_originals_are_read_only_when_every_binding_matches(
    failure: str | None,
) -> None:
    rows = _originals()
    store = WikiStore(cast(Any, _OriginalStore(rows)))
    snapshot = await store.source_units("document-a")
    assert snapshot is not None
    unit = snapshot.units[0]
    page = WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    if failure == "missing":
        rows.pop()
    elif failure is not None:
        key = {"body": "chunk_text", "document": "document_id", "version": "chunk_set_id"}[failure]
        rows[-1][key] = "changed"
    result = await store.read_sources([page], {"knowledge_base_id": "kb-a"})
    assert [chunk.chunk_id for chunk in result] == ([] if failure else unit.source_chunk_ids)


def test_multiple_originals_require_complete_hashes() -> None:
    unit = units()[0].model_dump()
    unit["source_chunk_ids"] = ["first", "second"]
    with pytest.raises(ValueError, match="個別のハッシュ"):
        WikiUnit.model_validate(unit)
    unit["source_chunk_hashes"] = {"first": fingerprint("本文")}
    with pytest.raises(ValueError, match="すべてのハッシュ"):
        WikiUnit.model_validate(unit)
