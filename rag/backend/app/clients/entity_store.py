"""実体の層（#1362）の Oracle の表と SQL。

- ``rag_entities``: 実体（chunk_set ごと。正規化した名前・表示名・種類・会社の名前）。文書の削除
  で消える（``ON DELETE CASCADE``）。chunk_set には FK を張らない（取込は chunk を保存した後で
  chunk_set の行を作る経路がある）。chunk_set の GC では chunk の削除で関連が消え、関連の無い実体は
  次の実体の保存（同じ文書）で消す。
- ``rag_entity_aliases``: 実体の別名（正規化した形。名寄せの join の key）。実体の削除で消える。
- ``rag_entity_chunks``: 実体と chunk の関連（定義する行 / 属性 / 言及）。実体と chunk の削除で消
  える（再索引で chunk を作り直すと、その chunk の関連も消える）。

実体の層は回答の検索の join に使うため、ナレッジベースの範囲を検索のときに chunk と文書の条件
（``_oracle_retrieval_where``）で決め、削除を FK で連動させる。

検索の SQL は ``app.rag.entity_expansion.EntityExpansionStore`` の 3 つ（起点の別名・名寄せ・1 段）
。どれも chunk と文書を検索と同じ見え方の条件で選び、範囲の外の chunk を返さない（途中の chunk
も同じ）。1 段は、実体の表と見え方の条件を 1 つの SQL で join せず、小さな SQL に分けて Python で
結合する（#5333。``EntityStore.entity_attribute_definition_chunks``）。

本文の見出し・列挙のラベル（別名の種類 ``label``。「原因 A」「手順 1」）は、別の文書が同じ別名をラ
ベル以外の種類（表の値・定義の形・ID や名前）で持ち、その文書の chunk が同じ見え方の条件の中にある
ときだけ使う（``_usable_alias_sql``。#1393）。1 つの文書の中だけで使う記号は、起点にも名寄せ・1 段の
経路にもしない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.clients.oracle import (
    _NOT_DOCUMENT_SUMMARY_CHUNK_SQL,
    _SERVED_CHUNK_SET_SQL,
    OracleClient,
    OracleConnectionProtocol,
    _current_tenant_id_hash,
    _execute,
    _executemany,
    _json_loads,
    _metadata_from_json,
    _oracle_in_predicate,
    _oracle_retrieval_where,
    _oracle_retrieval_where_split,
    _render_sql,
    _retrieved_chunk_from_row,
)
from app.rag.chunking import Chunk
from app.rag.entity_expansion import (
    MIN_QUESTION_ALIAS_CHARS,
    EntityDefinitionRow,
    EntitySeedRow,
)
from app.rag.entity_index import ALIAS_KIND_LABEL, EntityIndex
from app.schemas.search import RetrievedChunk

ENTITIES_TABLE = "rag_entities"
ENTITY_ALIASES_TABLE = "rag_entity_aliases"
ENTITY_CHUNKS_TABLE = "rag_entity_chunks"
# 質問の照合に渡す正規化した質問の長さの上限（bind の VARCHAR2 に収める）。
_QUESTION_KEY_MAX_CHARS = 1000
# 1 回の SQL の IN の値の数の上限（Oracle の IN の上限 1000 より小さく）。
_MAX_IN_VALUES = 500

_CHUNK_COLUMNS = """
                c.document_id,
                c.chunk_id,
                c.chunk_text,
                c.metadata_json,
                c.chunk_index,
                c.chunk_set_id,
                d.file_name,
                d.category_name,
                d.superseded_by_document_id,
                0 AS score"""


@dataclass(frozen=True)
class EntityRebuildTarget:
    """実体・別名索引を作り直す chunk_set（検索に出している版。#5178）。"""

    document_id: str
    chunk_set_id: str
    recipe_id: str | None
    extraction_recipe_id: str | None
    tenant_id_hash: str | None
    file_name: str
    content_type: str | None
    processing_config: dict[str, object]


@dataclass(frozen=True)
class _HopLink:
    """1 段の経路の 1 行（元の chunk の属性の実体 → 同じ別名の別の実体の「定義する行」）。"""

    via_chunk_id: str
    via_scope_label: str | None
    alias_key: str
    alias_kind: str
    entity_id: str
    document_id: str
    display_name: str
    scope_label: str | None
    chunk_id: str


def _batches(values: Sequence[str]) -> list[list[str]]:
    """IN の値を ``_MAX_IN_VALUES`` 件ずつに分ける（同じ値は 1 回だけ）。"""
    unique = list(dict.fromkeys(values))
    return [
        unique[start : start + _MAX_IN_VALUES] for start in range(0, len(unique), _MAX_IN_VALUES)
    ]


def _binds_in(sql: str, binds: Mapping[str, object]) -> dict[str, object]:
    """``sql`` が使う bind だけ（使わない bind を渡すと python-oracledb が失敗する）。"""
    return {key: value for key, value in binds.items() if re.search(rf":{key}\b", sql)}


def _usable_alias_sql(alias: str, entity: str, where_sql: str) -> str:
    """拡張に使える別名の条件（#1393）。

    ラベル（``label``。「原因 A」）の別名は、別の文書の実体が同じ別名をラベル以外の種類で持ち、その
    実体の chunk が検索と同じ見え方の条件（``where_sql``）の中にあるときだけ使う。``where_sql`` は
    ``c`` / ``d`` の別名を使うため、内側の副問い合わせでも同じ別名で chunk と文書を join する
    （外側の ``c`` / ``d`` は内側の別名で隠れる）。
    """
    return f"""(
                    {alias}.alias_kind <> '{ALIAS_KIND_LABEL}'
                    OR EXISTS (
                        SELECT 1
                        FROM {ENTITY_ALIASES_TABLE} label_a
                        JOIN {ENTITIES_TABLE} label_e ON label_e.entity_id = label_a.entity_id
                        JOIN {ENTITY_CHUNKS_TABLE} label_ec
                          ON label_ec.entity_id = label_e.entity_id
                        JOIN rag_chunks c ON c.chunk_id = label_ec.chunk_id
                        JOIN rag_documents d ON d.document_id = c.document_id
                        WHERE label_a.alias_key = {alias}.alias_key
                          AND label_a.alias_kind <> '{ALIAS_KIND_LABEL}'
                          AND label_e.document_id <> {entity}.document_id
                          AND {where_sql}
                    )
                )"""


def oracle_entity_schema_sql() -> str:
    """実体・別名・実体と chunk の関連の表の DDL（#1362）。"""
    return f"""
CREATE TABLE {ENTITIES_TABLE} (
    entity_id       VARCHAR2(64) PRIMARY KEY,
    chunk_set_id    VARCHAR2(64) NOT NULL,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    entity_key      VARCHAR2(400 CHAR) NOT NULL,
    display_name    VARCHAR2(400 CHAR) NOT NULL,
    entity_type     VARCHAR2(16) NOT NULL,
    scope_label     VARCHAR2(200 CHAR),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {ENTITIES_TABLE}_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT {ENTITIES_TABLE}_type_ck
        CHECK (entity_type IN ('record', 'defined', 'value', 'term'))
);

CREATE INDEX {ENTITIES_TABLE}_chunk_set_idx
    ON {ENTITIES_TABLE} (chunk_set_id);

CREATE INDEX {ENTITIES_TABLE}_document_idx
    ON {ENTITIES_TABLE} (document_id);

CREATE TABLE {ENTITY_ALIASES_TABLE} (
    entity_id   VARCHAR2(64) NOT NULL,
    alias_key   VARCHAR2(400 CHAR) NOT NULL,
    alias_text  VARCHAR2(400 CHAR) NOT NULL,
    alias_kind  VARCHAR2(16) NOT NULL,
    CONSTRAINT {ENTITY_ALIASES_TABLE}_pk PRIMARY KEY (entity_id, alias_key),
    CONSTRAINT {ENTITY_ALIASES_TABLE}_entity_fk
        FOREIGN KEY (entity_id) REFERENCES {ENTITIES_TABLE} (entity_id) ON DELETE CASCADE
);

CREATE INDEX {ENTITY_ALIASES_TABLE}_key_idx
    ON {ENTITY_ALIASES_TABLE} (alias_key, entity_id);

CREATE TABLE {ENTITY_CHUNKS_TABLE} (
    entity_id       VARCHAR2(64) NOT NULL,
    chunk_id        VARCHAR2(128) NOT NULL,
    chunk_role      VARCHAR2(16) NOT NULL,
    attribute_name  VARCHAR2(200 CHAR),
    CONSTRAINT {ENTITY_CHUNKS_TABLE}_pk PRIMARY KEY (entity_id, chunk_id),
    CONSTRAINT {ENTITY_CHUNKS_TABLE}_entity_fk
        FOREIGN KEY (entity_id) REFERENCES {ENTITIES_TABLE} (entity_id) ON DELETE CASCADE,
    CONSTRAINT {ENTITY_CHUNKS_TABLE}_chunk_fk
        FOREIGN KEY (chunk_id) REFERENCES rag_chunks (chunk_id) ON DELETE CASCADE,
    CONSTRAINT {ENTITY_CHUNKS_TABLE}_role_ck
        CHECK (chunk_role IN ('definition', 'attribute', 'mention'))
);

CREATE INDEX {ENTITY_CHUNKS_TABLE}_chunk_idx
    ON {ENTITY_CHUNKS_TABLE} (chunk_id, chunk_role);
""".strip()


class EntityStore:
    """実体の層の読み書き（``OracleClient`` の接続と transaction を使う）。"""

    def __init__(self, oracle: OracleClient | None = None) -> None:
        self._oracle = oracle or OracleClient()

    async def replace_chunk_set_entity_index(
        self, document_id: str, chunk_set_id: str, index: EntityIndex
    ) -> None:
        """chunk_set の実体を置き換える（前の実体・別名・関連は消してから入れる）。"""

        def operation(connection: OracleConnectionProtocol) -> None:
            _delete_chunk_set_entities(connection, chunk_set_id)
            _delete_unlinked_document_entities(connection, document_id)
            if not index.entities:
                return
            tenant_id_hash = _current_tenant_id_hash()
            _executemany(
                connection,
                f"""
                INSERT INTO {ENTITIES_TABLE} (
                    entity_id, chunk_set_id, document_id, tenant_id_hash,
                    entity_key, display_name, entity_type, scope_label
                ) VALUES (
                    :entity_id, :chunk_set_id, :document_id, :tenant_id_hash,
                    :entity_key, :display_name, :entity_type, :scope_label
                )
                """,
                [
                    {
                        "entity_id": entity.entity_id,
                        "chunk_set_id": chunk_set_id,
                        "document_id": document_id,
                        "tenant_id_hash": tenant_id_hash,
                        "entity_key": entity.entity_key,
                        "display_name": entity.display_name,
                        "entity_type": entity.entity_type,
                        "scope_label": entity.scope_label,
                    }
                    for entity in index.entities
                ],
            )
            aliases = [
                {
                    "entity_id": entity.entity_id,
                    "alias_key": alias.alias_key,
                    "alias_text": alias.alias_text,
                    "alias_kind": alias.alias_kind,
                }
                for entity in index.entities
                for alias in entity.aliases
            ]
            if aliases:
                _executemany(
                    connection,
                    f"""
                    INSERT INTO {ENTITY_ALIASES_TABLE} (entity_id, alias_key, alias_text,
                    alias_kind)
                    VALUES (:entity_id, :alias_key, :alias_text, :alias_kind)
                    """,
                    aliases,
                )
            if index.links:
                _executemany(
                    connection,
                    f"""
                    INSERT INTO {ENTITY_CHUNKS_TABLE} (
                        entity_id, chunk_id, chunk_role, attribute_name
                    ) VALUES (:entity_id, :chunk_id, :chunk_role, :attribute_name)
                    """,
                    [
                        {
                            "entity_id": link.entity_id,
                            "chunk_id": link.chunk_id,
                            "chunk_role": link.chunk_role,
                            "attribute_name": link.attribute_name,
                        }
                        for link in index.links
                    ],
                )

        await self._oracle._run_transaction(operation)

    async def delete_chunk_set_entity_index(self, chunk_set_id: str) -> None:
        """chunk_set の実体を消す（レシピで実体の抽出をやめたとき）。"""
        await self._oracle._run_transaction(
            lambda connection: _delete_chunk_set_entities(connection, chunk_set_id)
        )

    async def list_rebuild_targets(
        self,
        *,
        document_ids: Sequence[str] = (),
        knowledge_base_ids: Sequence[str] = (),
    ) -> list[EntityRebuildTarget]:
        """実体・別名索引を作り直す chunk_set を返す（#5178。取込済みの文書の作り直し）。

        対象は検索に出している chunk_set（有効な ``INDEXED`` の版と、レシピの無い旧データの配信中の
        版）だけ（作っている途中の候補は、索引の工程で作られる）。文書 ID・ナレッジベース ID の両方
        が空なら全件。両方を渡したときは、どちらかに当たる文書。
        """
        clauses: list[str] = []
        binds: dict[str, object] = {}
        if document_ids:
            predicate, document_binds = _oracle_in_predicate(
                "cs.document_id", "rebuild_document", document_ids
            )
            clauses.append(predicate)
            binds.update(document_binds)
        if knowledge_base_ids:
            predicate, kb_binds = _oracle_in_predicate(
                "member.knowledge_base_id", "rebuild_kb", knowledge_base_ids
            )
            clauses.append(
                "EXISTS (SELECT 1 FROM rag_document_knowledge_bases member "
                f"WHERE member.document_id = cs.document_id AND {predicate})"
            )
            binds.update(kb_binds)
        where = f"AND ({' OR '.join(clauses)})" if clauses else ""
        rows = await self._oracle._fetch_all(
            f"""
            SELECT cs.chunk_set_id, cs.document_id, cs.recipe_id, cs.extraction_recipe_id,
                   d.tenant_id_hash, d.file_name, d.content_type,
                   r.processing_config
            FROM rag_chunk_sets cs
            JOIN rag_documents d ON d.document_id = cs.document_id
            LEFT JOIN rag_document_recipes r ON r.recipe_id = cs.recipe_id
            WHERE {_SERVED_CHUNK_SET_SQL}
              {where}
            ORDER BY cs.document_id, cs.chunk_set_id
            """,
            binds,
        )
        return [
            EntityRebuildTarget(
                document_id=str(row["document_id"]),
                chunk_set_id=str(row["chunk_set_id"]),
                recipe_id=_optional_text(row.get("recipe_id")),
                extraction_recipe_id=_optional_text(row.get("extraction_recipe_id")),
                tenant_id_hash=_optional_text(row.get("tenant_id_hash")),
                file_name=str(row.get("file_name") or ""),
                content_type=_optional_text(row.get("content_type")),
                processing_config=_json_loads(row.get("processing_config")),
            )
            for row in rows
        ]

    async def chunk_set_source_chunks(self, chunk_set_id: str) -> list[Chunk]:
        """chunk_set の保存済みの chunk を、取込が実体を抜き出したときの形で読み順に返す。

        文書全体の要約の chunk（実体の索引の後に入れる chunk。#4793）は含めない。
        """
        rows = await self._oracle._fetch_all(
            f"""
            SELECT c.chunk_index, c.chunk_text, c.metadata_json
            FROM rag_chunks c
            WHERE c.chunk_set_id = :chunk_set_id
              AND {_NOT_DOCUMENT_SUMMARY_CHUNK_SQL}
            ORDER BY c.chunk_index
            """,
            {"chunk_set_id": chunk_set_id},
        )
        chunks: list[Chunk] = []
        for row in rows:
            text = str(row.get("chunk_text") or "")
            chunks.append(
                Chunk(
                    text=text,
                    index=_int(row.get("chunk_index")),
                    start_offset=0,
                    end_offset=len(text),
                    metadata={
                        key: value
                        for key, value in _metadata_from_json(row.get("metadata_json")).items()
                        if value is None or isinstance(value, str | int | float)
                    },
                )
            )
        return chunks

    async def extraction_title_texts(
        self, *, document_id: str, extraction_recipe_id: str | None
    ) -> list[str]:
        """抽出の先頭の 3 要素の本文（取込が実体の会社の名前を探す題名と同じ範囲）。"""
        if extraction_recipe_id is None:
            return []
        row = await self._oracle._fetch_one(
            """
            SELECT JSON_QUERY(
                       extraction_json, '$.elements[0 to 2].text'
                       RETURNING CLOB WITH ARRAY WRAPPER
                   ) AS title_texts
            FROM rag_document_extractions
            WHERE document_id = :document_id AND extraction_recipe_id = :extraction_recipe_id
            """,
            {"document_id": document_id, "extraction_recipe_id": extraction_recipe_id},
        )
        return _json_text_list(row.get("title_texts") if row else None)

    async def count_chunk_set_entities(self, chunk_set_id: str) -> int:
        """chunk_set の保存済みの実体の数。"""
        row = await self._oracle._fetch_one(
            f"SELECT COUNT(*) AS n FROM {ENTITIES_TABLE} WHERE chunk_set_id = :chunk_set_id",
            {"chunk_set_id": chunk_set_id},
        )
        return _int(row.get("n")) if row else 0

    async def entity_seed_aliases(
        self,
        filters: dict[str, str],
        *,
        seed_chunk_ids: Sequence[str],
        question_key: str,
        limit: int,
    ) -> list[EntitySeedRow]:
        """起点の実体（上位の chunk に関連づけた実体と、質問に別名が含まれる実体）のすべての別名。

        上位の chunk からは「定義する行」と「言及」の実体を起点にする（属性の実体は 1 段の拡張でた
        どる）。質問の照合は 2 文字以上の別名だけ（1 文字の略号は質問の任意の 1 文字に一致させない）
        。どちらも検索と同じ見え方の条件の chunk を持つ実体に限る。
        """
        where_sql, binds = _oracle_retrieval_where(filters)
        seeds = list(dict.fromkeys(seed_chunk_ids))[:_MAX_IN_VALUES]
        branches: list[str] = []
        if seeds:
            seed_in_sql, seed_binds = _oracle_in_predicate("ec.chunk_id", "entity_seed", seeds)
            binds.update(seed_binds)
            rank_case = " ".join(
                f"WHEN :entity_seed_{index} THEN {index}" for index in range(len(seeds))
            )
            branches.append(
                f"""
                SELECT ec.entity_id, CASE ec.chunk_id {rank_case} END AS seed_rank
                FROM {ENTITY_CHUNKS_TABLE} ec
                JOIN rag_chunks c ON c.chunk_id = ec.chunk_id
                JOIN rag_documents d ON d.document_id = c.document_id
                WHERE {where_sql}
                  AND {seed_in_sql}
                  AND ec.chunk_role IN ('definition', 'mention')
                """
            )
        question = question_key[:_QUESTION_KEY_MAX_CHARS]
        if len(question) >= MIN_QUESTION_ALIAS_CHARS:
            binds["entity_question_key"] = question
            binds["entity_min_alias_chars"] = MIN_QUESTION_ALIAS_CHARS
            branches.append(
                f"""
                SELECT qa.entity_id, NULL AS seed_rank
                FROM {ENTITY_ALIASES_TABLE} qa
                WHERE LENGTH(qa.alias_key) >= :entity_min_alias_chars
                  AND INSTR(:entity_question_key, qa.alias_key) > 0
                  AND EXISTS (
                      SELECT 1
                      FROM {ENTITY_CHUNKS_TABLE} ec
                      JOIN rag_chunks c ON c.chunk_id = ec.chunk_id
                      JOIN rag_documents d ON d.document_id = c.document_id
                      WHERE ec.entity_id = qa.entity_id
                        AND {where_sql}
                  )
                """
            )
        if not branches:
            return []
        binds.setdefault("entity_question_key", "")
        binds["entity_seed_limit"] = max(1, int(limit))
        rows = await self._oracle._fetch_all(
            f"""
            SELECT * FROM (
                SELECT
                    seed.entity_id,
                    alias.alias_key,
                    e.scope_label,
                    MAX(
                        CASE
                            WHEN LENGTH(alias.alias_key) >= {MIN_QUESTION_ALIAS_CHARS}
                             AND INSTR(:entity_question_key, alias.alias_key) > 0
                            THEN 1 ELSE 0
                        END
                    ) AS in_question,
                    MIN(seed.seed_rank) AS seed_rank
                FROM ({" UNION ALL ".join(branches)}) seed
                JOIN {ENTITIES_TABLE} e ON e.entity_id = seed.entity_id
                JOIN {ENTITY_ALIASES_TABLE} alias ON alias.entity_id = seed.entity_id
                WHERE {_usable_alias_sql("alias", "e", where_sql)}
                GROUP BY seed.entity_id, alias.alias_key, e.scope_label
                ORDER BY MIN(seed.seed_rank) NULLS FIRST, seed.entity_id, alias.alias_key
            ) WHERE ROWNUM <= :entity_seed_limit
            """,
            binds,
        )
        return [
            EntitySeedRow(
                entity_id=str(row["entity_id"]),
                alias_key=str(row["alias_key"]),
                scope_label=_optional_text(row.get("scope_label")),
                in_question=_int(row.get("in_question")) > 0,
                seed_rank=(
                    _int(row.get("seed_rank")) if row.get("seed_rank") is not None else None
                ),
            )
            for row in rows
        ]

    async def entity_definition_chunks(
        self, filters: dict[str, str], *, alias_keys: Sequence[str], limit: int
    ) -> list[EntityDefinitionRow]:
        """別名のどれかを「定義する行」に持つ chunk（名寄せ）。"""
        keys = list(dict.fromkeys(key for key in alias_keys if key))[:_MAX_IN_VALUES]
        if not keys:
            return []
        where_sql, binds = _oracle_retrieval_where(filters)
        alias_in_sql, alias_binds = _oracle_in_predicate("a.alias_key", "entity_alias", keys)
        binds.update(alias_binds)
        binds["entity_limit"] = max(1, int(limit))
        rows = await self._oracle._fetch_all(
            _render_sql(
                f"""
            SELECT * FROM (
                SELECT {_CHUNK_COLUMNS},
                    a.alias_key AS entity_match_key,
                    e.entity_id AS entity_id,
                    e.display_name AS entity_display_name,
                    e.scope_label AS entity_scope_label
                FROM {ENTITY_ALIASES_TABLE} a
                JOIN {ENTITIES_TABLE} e ON e.entity_id = a.entity_id
                JOIN {ENTITY_CHUNKS_TABLE} ec
                  ON ec.entity_id = a.entity_id AND ec.chunk_role = 'definition'
                JOIN rag_chunks c ON c.chunk_id = ec.chunk_id
                JOIN rag_documents d ON d.document_id = c.document_id
                WHERE {{where_sql}}
                  AND {alias_in_sql}
                  AND {_usable_alias_sql("a", "e", where_sql)}
                ORDER BY c.document_id, c.chunk_index, c.chunk_id, a.alias_key
            ) WHERE ROWNUM <= :entity_limit
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return [_definition_row(row) for row in rows]

    async def entity_attribute_definition_chunks(
        self, filters: dict[str, str], *, source_chunk_ids: Sequence[str], limit: int
    ) -> list[EntityDefinitionRow]:
        """chunk の属性の実体（台帳の行の「担当部署: 経」）の別名を「定義する行」に持つ chunk（1
        段）。

        実体の表と見え方の条件（``_oracle_retrieval_where``）を 1 つの SQL で join せず、小さな集合
        と bind の一覧で引く SQL に分ける（#5333）。

        1. 見え方の条件の文書の側だけで、見える文書の ID を読む。
        2. 実体の表: 元の chunk の属性の実体 → 同じ別名の別の実体 → その「定義する行」の chunk
           （``src.chunk_id`` の IN から引き、chunk の文書を 1 の一覧で絞る）。
        3. ラベルの別名（``label``）があれば、同じ別名をラベル以外の種類で持つ実体の文書と chunk
           （``alias_key`` の IN から引く。``_usable_alias_sql`` と同じ判定に使う）。
        4. 2・3 の chunk のうち、見え方の条件（文書と chunk の両側）の中にあるもの（``chunk_id`` の
           IN）。
        5. 選んだ上位 ``limit`` 件の chunk の本文（``chunk_id`` の IN）。

        結合・並べ替え（元の chunk・文書・chunk の順・chunk・別名）・上限は Python で行い、結果は
        1 つの SQL で join した形と同じにする。1 つの SQL では、optimizer が見え方の条件の文書の数を
        1 件と見積もり（ナレッジベースの所属・重複の正本・有効期間の副問い合わせ）、統計の更新の後の
        ハードパースで ``rag_entity_chunks`` の読み取りを nested loop の内側で繰り返す計画に
        変わって、1 回が 20 分を超えた。
        """
        sources = list(dict.fromkeys(source_chunk_ids))[:_MAX_IN_VALUES]
        if not sources:
            return []
        documents = await self._visible_document_ids(filters)
        if not documents:
            return []
        # 見える文書が多いときは 2・3 を文書で絞らない（4 で同じ条件を確かめるので結果は同じ）。
        document_filter = documents if len(documents) <= _MAX_IN_VALUES else None
        links = await self._attribute_hop_links(sources, document_filter)
        if not links:
            return []
        label_keys = list(
            dict.fromkeys(link.alias_key for link in links if link.alias_kind == ALIAS_KIND_LABEL)
        )
        label_evidence = (
            await self._label_evidence(label_keys, document_filter) if label_keys else []
        )
        candidate_ids = [link.chunk_id for link in links]
        candidate_ids.extend(chunk_id for _, _, chunk_id in label_evidence)
        visible = await self._visible_chunk_positions(filters, candidate_ids)
        # ラベルの別名を使える文書（別名 → 同じ別名をラベル以外で持ち、見える chunk のある文書）。
        label_documents: dict[str, set[str]] = {}
        for alias_key, document_id, chunk_id in label_evidence:
            if chunk_id in visible:
                label_documents.setdefault(alias_key, set()).add(document_id)

        def usable(link: _HopLink) -> bool:
            if link.alias_kind != ALIAS_KIND_LABEL:
                return True
            return any(
                document_id != link.document_id
                for document_id in label_documents.get(link.alias_key, ())
            )

        ordered = sorted(
            (link for link in links if link.chunk_id in visible and usable(link)),
            key=lambda link: (
                link.via_chunk_id,
                *visible[link.chunk_id],
                link.chunk_id,
                link.alias_key,
                link.entity_id,
            ),
        )[: max(1, int(limit))]
        if not ordered:
            return []
        chunks = await self._chunks_by_id([link.chunk_id for link in ordered])
        return [
            EntityDefinitionRow(
                chunk=chunks[link.chunk_id],
                match_key=link.alias_key,
                entity_id=link.entity_id,
                display_name=link.display_name,
                scope_label=link.scope_label,
                via_chunk_id=link.via_chunk_id,
                via_scope_label=link.via_scope_label,
            )
            for link in ordered
            if link.chunk_id in chunks
        ]

    async def _visible_document_ids(self, filters: dict[str, str]) -> list[str]:
        """見え方の条件の文書の側（tenant・権限・ナレッジベース・状態・有効期間）の文書の ID。"""
        document_sql, _, binds = _oracle_retrieval_where_split(filters)
        rows = await self._oracle._fetch_all(
            f"SELECT d.document_id FROM rag_documents d WHERE {document_sql}",
            _binds_in(document_sql, binds),
        )
        return [str(row["document_id"]) for row in rows]

    async def _attribute_hop_links(
        self, sources: Sequence[str], documents: Sequence[str] | None
    ) -> list[_HopLink]:
        """1 段の経路（実体の表）。元の chunk の属性の実体の別名を、別の実体が持つ組。"""
        source_in_sql, binds = _oracle_in_predicate("src.chunk_id", "entity_source", sources)
        document_join = ""
        if documents is not None:
            document_in_sql, document_binds = _oracle_in_predicate(
                "c.document_id", "entity_document", documents
            )
            binds.update(document_binds)
            document_join = f"JOIN rag_chunks c ON c.chunk_id = ec.chunk_id AND {document_in_sql}"
        rows = await self._oracle._fetch_all(
            f"""
            SELECT
                src.chunk_id AS via_chunk_id,
                src_e.scope_label AS via_scope_label,
                a.alias_key AS alias_key,
                a.alias_kind AS alias_kind,
                e.entity_id AS entity_id,
                e.document_id AS document_id,
                e.display_name AS display_name,
                e.scope_label AS scope_label,
                ec.chunk_id AS chunk_id
            FROM {ENTITY_CHUNKS_TABLE} src
            JOIN {ENTITIES_TABLE} src_e ON src_e.entity_id = src.entity_id
            JOIN {ENTITY_ALIASES_TABLE} src_a ON src_a.entity_id = src.entity_id
            JOIN {ENTITY_ALIASES_TABLE} a
              ON a.alias_key = src_a.alias_key AND a.entity_id <> src.entity_id
            JOIN {ENTITIES_TABLE} e ON e.entity_id = a.entity_id
            JOIN {ENTITY_CHUNKS_TABLE} ec
              ON ec.entity_id = a.entity_id AND ec.chunk_role = 'definition'
            {document_join}
            WHERE src.chunk_role = 'attribute'
              AND {source_in_sql}
            """,
            binds,
        )
        return [
            _HopLink(
                via_chunk_id=str(row["via_chunk_id"]),
                via_scope_label=_optional_text(row.get("via_scope_label")),
                alias_key=str(row["alias_key"]),
                alias_kind=str(row["alias_kind"]),
                entity_id=str(row["entity_id"]),
                document_id=str(row["document_id"]),
                display_name=str(row.get("display_name") or ""),
                scope_label=_optional_text(row.get("scope_label")),
                chunk_id=str(row["chunk_id"]),
            )
            for row in rows
        ]

    async def _label_evidence(
        self, alias_keys: Sequence[str], documents: Sequence[str] | None
    ) -> list[tuple[str, str, str]]:
        """ラベルの別名をラベル以外の種類で持つ実体の (別名, 文書 ID, chunk ID)。"""
        evidence: list[tuple[str, str, str]] = []
        for batch in _batches(alias_keys):
            alias_in_sql, binds = _oracle_in_predicate("label_a.alias_key", "entity_label", batch)
            document_join = ""
            if documents is not None:
                document_in_sql, document_binds = _oracle_in_predicate(
                    "c.document_id", "entity_document", documents
                )
                binds.update(document_binds)
                document_join = (
                    f"JOIN rag_chunks c ON c.chunk_id = label_ec.chunk_id AND {document_in_sql}"
                )
            rows = await self._oracle._fetch_all(
                f"""
                SELECT DISTINCT
                    label_a.alias_key AS alias_key,
                    label_e.document_id AS document_id,
                    label_ec.chunk_id AS chunk_id
                FROM {ENTITY_ALIASES_TABLE} label_a
                JOIN {ENTITIES_TABLE} label_e ON label_e.entity_id = label_a.entity_id
                JOIN {ENTITY_CHUNKS_TABLE} label_ec ON label_ec.entity_id = label_e.entity_id
                {document_join}
                WHERE {alias_in_sql}
                  AND label_a.alias_kind <> '{ALIAS_KIND_LABEL}'
                """,
                binds,
            )
            evidence.extend(
                (str(row["alias_key"]), str(row["document_id"]), str(row["chunk_id"]))
                for row in rows
            )
        return evidence

    async def _visible_chunk_positions(
        self, filters: dict[str, str], chunk_ids: Sequence[str]
    ) -> dict[str, tuple[str, int | float]]:
        """見え方の条件の中にある chunk の ID → (文書 ID, chunk の順)。"""
        positions: dict[str, tuple[str, int | float]] = {}
        for batch in _batches(chunk_ids):
            where_sql, binds = _oracle_retrieval_where(filters)
            chunk_in_sql, chunk_binds = _oracle_in_predicate("c.chunk_id", "entity_chunk", batch)
            binds.update(chunk_binds)
            rows = await self._oracle._fetch_all(
                _render_sql(
                    f"""
                SELECT c.chunk_id, c.document_id, c.chunk_index
                FROM rag_chunks c
                WHERE {chunk_in_sql}
                  AND {{where_sql}}
                """,
                    where_sql=where_sql,
                ),
                binds,
            )
            for row in rows:
                index = row.get("chunk_index")
                # Oracle の昇順と同じく、順の無い chunk は後ろに置く。
                positions[str(row["chunk_id"])] = (
                    str(row["document_id"]),
                    _int(index) if index is not None else float("inf"),
                )
        return positions

    async def _chunks_by_id(self, chunk_ids: Sequence[str]) -> dict[str, RetrievedChunk]:
        """chunk の ID → 根拠の chunk（見え方の条件は ``_visible_chunk_positions`` で確かめた）。"""
        chunks: dict[str, RetrievedChunk] = {}
        for batch in _batches(chunk_ids):
            chunk_in_sql, binds = _oracle_in_predicate("c.chunk_id", "entity_chunk", batch)
            rows = await self._oracle._fetch_all(
                f"""
                SELECT {_CHUNK_COLUMNS}
                FROM rag_chunks c
                JOIN rag_documents d ON d.document_id = c.document_id
                WHERE {chunk_in_sql}
                """,
                binds,
            )
            for row in rows:
                chunk = _retrieved_chunk_from_row(row)
                chunks[chunk.chunk_id] = chunk
        return chunks


def _delete_chunk_set_entities(connection: OracleConnectionProtocol, chunk_set_id: str) -> None:
    # 別名と関連は FK の ON DELETE CASCADE で一緒に消える。
    _execute(
        connection,
        f"DELETE FROM {ENTITIES_TABLE} WHERE chunk_set_id = :chunk_set_id",
        {"chunk_set_id": chunk_set_id},
    )


def _delete_unlinked_document_entities(
    connection: OracleConnectionProtocol, document_id: str
) -> None:
    # 文書の古い chunk_set（GC で chunk が消えた版）の、関連の無い実体を消す（検索に使われない行）。
    _execute(
        connection,
        f"""
        DELETE FROM {ENTITIES_TABLE} e
        WHERE e.document_id = :document_id
          AND NOT EXISTS (
              SELECT 1 FROM {ENTITY_CHUNKS_TABLE} ec WHERE ec.entity_id = e.entity_id
          )
        """,
        {"document_id": document_id},
    )


def _definition_row(row: Mapping[str, object]) -> EntityDefinitionRow:
    return EntityDefinitionRow(
        chunk=_retrieved_chunk_from_row(row),
        match_key=str(row["entity_match_key"]),
        entity_id=str(row["entity_id"]),
        display_name=str(row.get("entity_display_name") or ""),
        scope_label=_optional_text(row.get("entity_scope_label")),
        via_chunk_id=_optional_text(row.get("entity_via_chunk_id")),
        via_scope_label=_optional_text(row.get("entity_via_scope_label")),
    )


def _json_text_list(value: object) -> list[str]:
    if value is None:
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if isinstance(item, str)]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "ENTITIES_TABLE",
    "ENTITY_ALIASES_TABLE",
    "ENTITY_CHUNKS_TABLE",
    "EntityRebuildTarget",
    "EntityStore",
    "oracle_entity_schema_sql",
]
