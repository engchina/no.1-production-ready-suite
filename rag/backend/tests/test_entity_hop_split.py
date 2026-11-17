"""実体の 1 段の拡張の SQL を分けた形(#5333)が、1 つの SQL で join した形と同じ結果を返す。

合成の実体の表・chunk・文書を Python の表に持ち、分けた各 SQL への応答(替え玉の ``_fetch_all``)と、
分ける前の 1 つの SQL の意味(元の chunk の属性の実体 → 同じ別名の別の実体 → 「定義する行」の
chunk、見え方の条件、ラベルの別名の条件、並べ替えと上限)を同じ表から作って比べる。DB には接続しない
(実 Oracle の比較は ``test_entity_layer_oracle.py``)。
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.clients import entity_store as entity_store_module
from app.clients.entity_store import EntityStore
from app.rag.entity_index import ALIAS_KIND_LABEL


@dataclass
class _Corpus:
    """合成の表。chunk は (文書, 順, chunk の側の見え方, 本文)。"""

    visible_documents: set[str] = field(default_factory=set)
    documents: list[str] = field(default_factory=list)
    chunks: dict[str, tuple[str, int | None, bool, str]] = field(default_factory=dict)
    entities: dict[str, tuple[str, str, str | None]] = field(default_factory=dict)
    aliases: list[tuple[str, str, str]] = field(default_factory=list)
    links: list[tuple[str, str, str]] = field(default_factory=list)

    def chunk_visible(self, chunk_id: str) -> bool:
        document_id, _, chunk_side, _ = self.chunks[chunk_id]
        return chunk_side and document_id in self.visible_documents


def _binds(binds: Mapping[str, object], prefix: str) -> list[str]:
    return [str(value) for key, value in binds.items() if re.fullmatch(rf"{prefix}_\d+", key)]


class _FakeOracle:
    """分けた SQL に、合成の表から答える(SQL の種類は文の形で見分ける)。"""

    def __init__(self, corpus: _Corpus) -> None:
        self.corpus = corpus
        self.statements: list[str] = []

    async def _fetch_all(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        corpus = self.corpus
        binds = binds or {}
        self.statements.append(statement)
        documents = set(_binds(binds, "entity_document"))
        if statement.startswith("SELECT d.document_id FROM rag_documents d WHERE"):
            return [{"document_id": d} for d in corpus.documents if d in corpus.visible_documents]
        if f"FROM {entity_store_module.ENTITY_CHUNKS_TABLE} src" in statement:
            sources = set(_binds(binds, "entity_source"))
            rows: list[dict[str, object]] = []
            for src_entity, src_chunk, role in corpus.links:
                if role != "attribute" or src_chunk not in sources:
                    continue
                for alias_entity, key, _ in corpus.aliases:
                    if alias_entity != src_entity:
                        continue
                    for entity_id, other_key, kind in corpus.aliases:
                        if other_key != key or entity_id == src_entity:
                            continue
                        for link_entity, chunk_id, link_role in corpus.links:
                            if link_entity != entity_id or link_role != "definition":
                                continue
                            if documents and corpus.chunks[chunk_id][0] not in documents:
                                continue
                            document_id, display_name, scope_label = corpus.entities[entity_id]
                            rows.append(
                                {
                                    "via_chunk_id": src_chunk,
                                    "via_scope_label": corpus.entities[src_entity][2],
                                    "alias_key": key,
                                    "alias_kind": kind,
                                    "entity_id": entity_id,
                                    "document_id": document_id,
                                    "display_name": display_name,
                                    "scope_label": scope_label,
                                    "chunk_id": chunk_id,
                                }
                            )
            return rows
        if f"FROM {entity_store_module.ENTITY_ALIASES_TABLE} label_a" in statement:
            keys = set(_binds(binds, "entity_label"))
            found = {
                (key, corpus.entities[entity_id][0], chunk_id)
                for entity_id, key, kind in corpus.aliases
                if key in keys and kind != ALIAS_KIND_LABEL
                for link_entity, chunk_id, _ in corpus.links
                if link_entity == entity_id
                and (not documents or corpus.chunks[chunk_id][0] in documents)
            }
            return [{"alias_key": k, "document_id": d, "chunk_id": c} for k, d, c in found]
        chunk_ids = _binds(binds, "entity_chunk")
        if "SELECT c.chunk_id, c.document_id, c.chunk_index" in statement:
            return [
                {
                    "chunk_id": chunk_id,
                    "document_id": corpus.chunks[chunk_id][0],
                    "chunk_index": corpus.chunks[chunk_id][1],
                }
                for chunk_id in chunk_ids
                if corpus.chunk_visible(chunk_id)
            ]
        if "c.chunk_text" in statement:
            return [
                {
                    "document_id": corpus.chunks[chunk_id][0],
                    "chunk_id": chunk_id,
                    "chunk_text": corpus.chunks[chunk_id][3],
                    "metadata_json": None,
                    "chunk_index": corpus.chunks[chunk_id][1],
                    "chunk_set_id": None,
                    "file_name": f"{corpus.chunks[chunk_id][0]}.pdf",
                    "category_name": None,
                    "superseded_by_document_id": None,
                    "score": 0,
                }
                for chunk_id in chunk_ids
            ]
        raise AssertionError(f"想定していない SQL: {statement}")


def _reference(corpus: _Corpus, sources: Sequence[str], limit: int) -> list[tuple[Any, ...]]:
    """分ける前の 1 つの SQL の意味(``_usable_alias_sql`` を含む)。行は比べる値の組。"""
    unique_sources = list(dict.fromkeys(sources))
    rows: list[tuple[Any, ...]] = []
    for src_entity, src_chunk, role in corpus.links:
        if role != "attribute" or src_chunk not in unique_sources:
            continue
        for alias_entity, key, _ in corpus.aliases:
            if alias_entity != src_entity:
                continue
            for entity_id, other_key, kind in corpus.aliases:
                if other_key != key or entity_id == src_entity:
                    continue
                entity_document = corpus.entities[entity_id][0]
                if kind == ALIAS_KIND_LABEL and not any(
                    label_key == key
                    and label_kind != ALIAS_KIND_LABEL
                    and corpus.entities[label_entity][0] != entity_document
                    and corpus.chunk_visible(label_chunk)
                    for label_entity, label_key, label_kind in corpus.aliases
                    for link_entity, label_chunk, _ in corpus.links
                    if link_entity == label_entity
                ):
                    continue
                for link_entity, chunk_id, link_role in corpus.links:
                    if link_entity != entity_id or link_role != "definition":
                        continue
                    if not corpus.chunk_visible(chunk_id):
                        continue
                    document_id, index, _, text = corpus.chunks[chunk_id]
                    _, display_name, scope_label = corpus.entities[entity_id]
                    rows.append(
                        (
                            src_chunk,
                            document_id,
                            index if index is not None else float("inf"),
                            chunk_id,
                            key,
                            entity_id,
                            display_name,
                            scope_label,
                            corpus.entities[src_entity][2],
                            text,
                        )
                    )
    rows.sort(key=lambda row: row[:6])
    return rows[: max(1, limit)]


def _rows(result: Sequence[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            row.via_chunk_id,
            row.chunk.document_id,
            row.chunk.metadata.get("chunk_index", float("inf")),
            row.chunk.chunk_id,
            row.match_key,
            row.entity_id,
            row.display_name,
            row.scope_label,
            row.via_scope_label,
            row.chunk.text,
        )
        for row in result
    ]


def _random_corpus(seed: int) -> _Corpus:
    rng = random.Random(seed)
    corpus = _Corpus()
    corpus.documents = [f"doc-{n}" for n in range(6)]
    corpus.visible_documents = set(rng.sample(corpus.documents, 4))
    for document_id in corpus.documents:
        for position in range(6):
            chunk_id = f"{document_id}:c{position}"
            # 順の無い chunk(旧い取込の行)と、chunk の側で見えない chunk(有効でない chunk_set)。
            index = None if rng.random() < 0.1 else position
            corpus.chunks[chunk_id] = (document_id, index, rng.random() > 0.15, f"本文 {chunk_id}")
    keys = [f"合成{n}" for n in range(6)]
    for n in range(24):
        entity_id = f"entity-{n:02d}"
        document_id = rng.choice(corpus.documents)
        scope = rng.choice([None, "範囲 A", "範囲 B"])
        corpus.entities[entity_id] = (document_id, f"実体 {n}", scope)
        for key in rng.sample(keys, rng.randint(1, 2)):
            kind = ALIAS_KIND_LABEL if rng.random() < 0.3 else "name"
            corpus.aliases.append((entity_id, key, kind))
        own_chunks = [c for c in corpus.chunks if corpus.chunks[c][0] == document_id]
        for chunk_id in rng.sample(own_chunks, rng.randint(1, 3)):
            corpus.links.append((entity_id, chunk_id, rng.choice(["definition", "attribute"])))
    # 同じ実体と chunk の関連は 1 行(主キー)。
    corpus.links = list({(e, c): (e, c, r) for e, c, r in corpus.links}.values())
    return corpus


@pytest.mark.parametrize("seed", range(12), ids=[f"seed{n}" for n in range(12)])
@pytest.mark.parametrize("in_limit", [500, 3], ids=["in500", "in3"])
async def test_split_hop_matches_single_join(
    monkeypatch: pytest.MonkeyPatch, seed: int, in_limit: int
) -> None:
    # IN の上限を小さくして、値を分けて読む経路(文書で絞らない経路を含む)も確かめる。
    monkeypatch.setattr(entity_store_module, "_MAX_IN_VALUES", in_limit)
    corpus = _random_corpus(seed)
    sources = sorted({c for _, c, role in corpus.links if role == "attribute"})
    # 並びが決まる上限の無い件数で、行の値と並びを比べる。
    store = EntityStore(_FakeOracle(corpus))  # type: ignore[arg-type]
    full = await store.entity_attribute_definition_chunks(
        {"knowledge_base_id": "kb-a"}, source_chunk_ids=sources, limit=10_000
    )
    expected = _reference(corpus, sources[:in_limit], 10_000)
    assert _rows(full) == expected
    # 上限で切るときは並べ替えの key(元の chunk・文書・順・chunk・別名)の並びが同じ。
    for limit in (1, 5, 24):
        cut = await store.entity_attribute_definition_chunks(
            {"knowledge_base_id": "kb-a"}, source_chunk_ids=sources, limit=limit
        )
        assert [row[:5] for row in _rows(cut)] == [
            row[:5] for row in _reference(corpus, sources[:in_limit], limit)
        ]


async def test_label_alias_needs_visible_non_label_alias_in_another_document() -> None:
    """ラベルの別名は、別の文書の見える chunk が同じ別名をラベル以外で持つときだけ使う。"""
    corpus = _Corpus(
        documents=["doc-a", "doc-b", "doc-c"],
        visible_documents={"doc-a", "doc-b", "doc-c"},
        chunks={
            "doc-a:1": ("doc-a", 1, True, "元の行"),
            "doc-b:1": ("doc-b", 1, True, "ラベルの定義"),
            "doc-c:1": ("doc-c", 1, False, "見えない chunk の定義"),
        },
        entities={
            "src": ("doc-a", "元", None),
            "label": ("doc-b", "ラベル", None),
            "named": ("doc-c", "名前", None),
        },
        aliases=[("src", "合成a", "name"), ("label", "合成a", ALIAS_KIND_LABEL)],
        links=[("src", "doc-a:1", "attribute"), ("label", "doc-b:1", "definition")],
    )
    store = EntityStore(_FakeOracle(corpus))  # type: ignore[arg-type]

    # 同じ別名をラベル以外で持つのは doc-a の「src」だけ(別の文書・見える chunk)なので使う。
    rows = await store.entity_attribute_definition_chunks({}, source_chunk_ids=["doc-a:1"], limit=5)
    assert [(row.chunk.chunk_id, row.match_key) for row in rows] == [("doc-b:1", "合成a")]

    # ラベル以外の別名が同じ文書の中だけ・見えない chunk だけなら使わない。
    corpus.aliases[0] = ("src", "合成b", "name")
    corpus.aliases.append(("named", "合成a", "name"))
    corpus.links.append(("named", "doc-c:1", "mention"))
    corpus.aliases.append(("src", "合成a", ALIAS_KIND_LABEL))
    rows = await store.entity_attribute_definition_chunks({}, source_chunk_ids=["doc-a:1"], limit=5)
    assert _rows(rows) == _reference(corpus, ["doc-a:1"], 5) == []


async def test_no_visible_document_reads_no_entity_tables() -> None:
    corpus = _Corpus(documents=["doc-a"], visible_documents=set())
    oracle = _FakeOracle(corpus)
    store = EntityStore(oracle)  # type: ignore[arg-type]

    assert await store.entity_attribute_definition_chunks({}, source_chunk_ids=["x"], limit=5) == []
    assert len(oracle.statements) == 1
