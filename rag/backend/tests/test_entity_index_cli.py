"""実体・別名索引の作り直しの CLI（#5178）を、合成の chunk と Python の代役の store で確かめる。

実 Oracle の SQL（対象の選び方・chunk の読み出し・置き換え）は ``test_entity_layer_oracle.py``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from app.clients.entity_store import EntityRebuildTarget
from app.config import Settings
from app.rag import entity_index_cli
from app.rag.chunking import Chunk
from app.rag.entity_index import EntityIndex, build_entity_index
from app.rag.entity_index_cli import rebuild_entity_indexes, recipe_settings
from app.rag.request_context import current_audit_request_context

_TEXT = (
    "本書は、データ変換ユーティリティ Kawa Convert（以下、本ツール）の説明です。\n"
    "This section describes the REST API."
)


def _chunk(text: str, index: int, **metadata: str) -> Chunk:
    return Chunk(
        text=text,
        index=index,
        start_offset=0,
        end_offset=len(text),
        metadata={"content_kind": "text", **metadata},
    )


def _target(document_id: str, chunk_set_id: str, **config: object) -> EntityRebuildTarget:
    return EntityRebuildTarget(
        document_id=document_id,
        chunk_set_id=chunk_set_id,
        recipe_id=f"recipe-{document_id}",
        extraction_recipe_id=f"ex-{document_id}",
        tenant_id_hash=f"tenant-{document_id}",
        file_name="manual.md",
        content_type="text/markdown",
        processing_config=dict(config),
    )


@dataclass
class _Store:
    targets: list[EntityRebuildTarget]
    chunks: dict[str, list[Chunk]]
    titles: dict[str, list[str]] = field(default_factory=dict)
    saved: dict[str, EntityIndex] = field(default_factory=dict)
    tenants: dict[str, str | None] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    requested: list[tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)

    async def list_rebuild_targets(
        self, *, document_ids: Sequence[str] = (), knowledge_base_ids: Sequence[str] = ()
    ) -> list[EntityRebuildTarget]:
        self.requested.append((tuple(document_ids), tuple(knowledge_base_ids)))
        return list(self.targets)

    async def chunk_set_source_chunks(self, chunk_set_id: str) -> list[Chunk]:
        if chunk_set_id in self.failing:
            raise RuntimeError("読み出しの失敗")
        return list(self.chunks.get(chunk_set_id, []))

    async def extraction_title_texts(
        self, *, document_id: str, extraction_recipe_id: str | None
    ) -> list[str]:
        return list(self.titles.get(document_id, []))

    async def count_chunk_set_entities(self, chunk_set_id: str) -> int:
        index = self.saved.get(chunk_set_id)
        return len(index.entities) if index is not None else 0

    async def replace_chunk_set_entity_index(
        self, document_id: str, chunk_set_id: str, index: EntityIndex
    ) -> None:
        self.saved[chunk_set_id] = index
        self.tenants[chunk_set_id] = current_audit_request_context().tenant_id_hash

    async def delete_chunk_set_entity_index(self, chunk_set_id: str) -> None:
        self.deleted.append(chunk_set_id)
        self.saved.pop(chunk_set_id, None)


def _settings(target: EntityRebuildTarget) -> Settings:
    return Settings(
        _env_file=None,
        rag_entity_index_enabled=bool(target.processing_config.get("entity_index_enabled", True)),
    )


def _store(*targets: EntityRebuildTarget) -> _Store:
    return _Store(
        targets=list(targets),
        chunks={target.chunk_set_id: [_chunk(_TEXT, 0), _chunk("付録", 1)] for target in targets},
    )


async def test_rebuild_replaces_the_chunk_set_rows_from_saved_chunks() -> None:
    target = _target("doc-1", "cs-1")
    store = _store(target)

    summary = await rebuild_entity_indexes(store, _settings, dry_run=False)

    expected = build_entity_index(
        document_id="doc-1", chunks=store.chunks["cs-1"], chunk_set_id="cs-1"
    )
    assert store.saved["cs-1"] == expected
    names = {entity.display_name for entity in store.saved["cs-1"].entities}
    assert "データ変換ユーティリティ Kawa Convert" in names
    assert not {"ータ変換ユーティリティ Kawa Convert", "This"} & names
    assert (summary.chunk_sets, summary.rebuilt, summary.cleared) == (1, 1, 0)
    assert summary.entities == len(expected.entities)
    assert summary.aliases == expected.alias_count
    assert summary.links == len(expected.links)
    # 実体の行は文書の tenant で保存する。
    assert store.tenants["cs-1"] == "tenant-doc-1"


async def test_dry_run_counts_without_writing() -> None:
    store = _store(_target("doc-1", "cs-1"), _target("doc-2", "cs-2", entity_index_enabled=False))
    store.saved["cs-2"] = EntityIndex()

    summary = await rebuild_entity_indexes(store, _settings, dry_run=True)

    assert (summary.chunk_sets, summary.rebuilt, summary.cleared) == (2, 1, 1)
    assert summary.entities > 0
    assert "cs-1" not in store.saved
    assert store.deleted == []


async def test_recipe_without_the_entity_index_clears_the_rows() -> None:
    target = _target("doc-1", "cs-1", entity_index_enabled=False)
    store = _store(target)
    store.saved["cs-1"] = build_entity_index(
        document_id="doc-1", chunks=store.chunks["cs-1"], chunk_set_id="cs-1"
    )
    before = len(store.saved["cs-1"].entities)

    summary = await rebuild_entity_indexes(store, _settings, dry_run=False)

    assert store.deleted == ["cs-1"]
    assert "cs-1" not in store.saved
    assert (summary.rebuilt, summary.cleared, summary.entities_before) == (0, 1, before)


async def test_rebuilding_twice_gives_the_same_rows() -> None:
    store = _store(_target("doc-1", "cs-1"))
    store.titles["doc-1"] = ["サンプル社 取扱説明書"]

    await rebuild_entity_indexes(store, _settings, dry_run=False)
    first = store.saved["cs-1"]
    second_summary = await rebuild_entity_indexes(store, _settings, dry_run=False)

    assert store.saved["cs-1"] == first
    assert {entity.scope_label for entity in first.entities} == {"サンプル社"}
    assert second_summary.entities_before == second_summary.entities == len(first.entities)


async def test_one_failure_is_recorded_and_the_rest_continue() -> None:
    store = _store(_target("doc-1", "cs-1"), _target("doc-2", "cs-2"))
    store.failing.add("cs-1")

    summary = await rebuild_entity_indexes(store, _settings, dry_run=False)

    assert summary.failed == [
        {"document_id": "doc-1", "chunk_set_id": "cs-1", "error_type": "RuntimeError"}
    ]
    assert set(store.saved) == {"cs-2"}
    # 文書ごとの tenant の文脈は、失敗しても元に戻す。
    assert current_audit_request_context().tenant_id_hash is None


async def test_selection_is_passed_to_the_store() -> None:
    store = _store()

    await rebuild_entity_indexes(
        store, _settings, dry_run=True, document_ids=["doc-1"], knowledge_base_ids=["kb-1"]
    )

    assert store.requested == [(("doc-1",), ("kb-1",))]


@pytest.mark.parametrize(
    "argv",
    [["rebuild"], ["rebuild", "--all", "--document-id", "doc-1"]],
    ids=["no-selection", "all-with-ids"],
)
def test_main_requires_exactly_one_selection(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert entity_index_cli.main(argv) == 2
    assert "--all" in capsys.readouterr().err


def test_main_prints_the_summary_and_fails_on_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(_target("doc-1", "cs-1"), _target("doc-2", "cs-2"))
    store.failing.add("cs-2")

    async def fake_run(args: object) -> entity_index_cli.RebuildSummary:
        return await rebuild_entity_indexes(store, _settings, dry_run=True)

    monkeypatch.setattr(entity_index_cli, "_run", fake_run)

    assert entity_index_cli.main(["rebuild", "--all", "--dry-run", "--details"]) == 1
    output = capsys.readouterr().out
    assert '"rebuilt": 1' in output
    assert '"chunk_set_id": "cs-1"' in output


def test_recipe_settings_follow_the_document_recipe() -> None:
    disabled = recipe_settings(_target("doc-1", "cs-1", entity_index_enabled=False))
    enabled = recipe_settings(
        _target("doc-1", "cs-1", entity_index_enabled=True, entity_attribute_columns=["担当部署"])
    )

    assert disabled.rag_entity_index_enabled is False
    assert enabled.rag_entity_index_enabled is True
    assert enabled.rag_entity_attribute_columns == ["担当部署"]
