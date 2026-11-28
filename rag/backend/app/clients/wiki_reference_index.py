"""公開済みの案内の、別の文書への参照（#5784）。

文書をまたぐ関連は、その文書の内容だけから取る（ほかの文書を見る判定はしない）。参照は 2 つを
合わせる。

- 案内の生成が返した参照（``navigation.references``。表記が原文にあるものだけ。関係の種類つき）。
- 取込で決定論に取り出した参照（chunk の metadata ``reference_targets_json`` の、別の文書を
  指すもの。関係は ``reference``）。

参照先の文書は保存しない（KB は純スコープで、所属を変えても参照に波及しない）。読むときに今の KB の
文書の名前と照合する。参照元を逆に引けるよう、参照先の名前の比較形（``target_key``）を持つ。
公開と同じ取引で文書ごとに置き換え、案内の削除（外部キー）で消える。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.clients.oracle import _execute, _fetch_all, _fetch_one, _json_list, _json_loads
from app.rag.cross_references import (
    REFERENCE_TARGETS_KEY,
    ReferenceTarget,
    document_name_keys,
    document_name_rank,
    document_reference_keys,
    reference_name_key,
    title_key,
)

REFERENCE_SCHEMA = """
CREATE TABLE rag_wiki_references (
    page_id VARCHAR2(64) NOT NULL REFERENCES rag_wiki_pages(page_id) ON DELETE CASCADE,
    ordinal NUMBER NOT NULL,
    document_id VARCHAR2(64) NOT NULL,
    unit_id VARCHAR2(64) NOT NULL,
    target_name VARCHAR2(400) NOT NULL,
    target_key VARCHAR2(400) NOT NULL,
    target_section VARCHAR2(400),
    relation VARCHAR2(32) NOT NULL,
    CONSTRAINT rag_wiki_reference_pk PRIMARY KEY (page_id,ordinal)
);
CREATE INDEX rag_wiki_reference_key_idx ON rag_wiki_references(target_key);
CREATE INDEX rag_wiki_reference_doc_idx ON rag_wiki_references(document_id);
""".strip()


def _text_references(metadata: object) -> list[tuple[str, str, str]]:
    """chunk の metadata の、別の文書を指す決定論の参照（文書名・章節の表記・関係）。"""
    data = _json_loads(metadata) if not isinstance(metadata, dict) else metadata
    raw = data.get(REFERENCE_TARGETS_KEY) if isinstance(data, dict) else None
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    found = []
    for item in items if isinstance(items, list) else ():
        target = ReferenceTarget.from_dict(item) if isinstance(item, dict) else None
        if target is None or not target.document_title:
            continue
        section = "" if target.kind == "document" else target.label
        found.append((target.document_title, section, "reference"))
    return found


def model_references(value: object) -> list[tuple[str, str, str]]:
    """案内の生成が返した参照（``navigation.references`` の JSON。文書名・章節の表記・関係）。"""
    if hasattr(value, "read"):
        value = value.read()
    try:
        items = json.loads(value) if isinstance(value, str | bytes) else value
    except ValueError:
        return []
    return [
        (item["document"], str(item.get("section") or ""), str(item.get("relation") or ""))
        for item in (items if isinstance(items, list) else ())
        if isinstance(item, dict) and isinstance(item.get("document"), str) and item.get("relation")
    ]


def index_references(connection: Any, document_id: str, version: str) -> None:
    """公開と同じ取引で、文書の参照を置き換える。"""
    _execute(
        connection, "DELETE FROM rag_wiki_references WHERE document_id=:doc", {"doc": document_id}
    )
    pages = _fetch_all(
        connection,
        """SELECT p.page_id,p.unit_id,
        JSON_QUERY(p.page_json,'$.navigation.references' RETURNING CLOB) AS refs,
        JSON_QUERY(p.page_json,'$.unit.source_chunk_ids' RETURNING CLOB) AS chunks
        FROM rag_wiki_pages p WHERE p.document_id=:doc AND p.source_fingerprint=:version""",
        {"doc": document_id, "version": version},
    )
    metadata = {
        str(row["chunk_id"]): row["metadata_json"]
        for row in _fetch_all(
            connection,
            "SELECT chunk_id,metadata_json FROM rag_chunks WHERE document_id=:doc",
            {"doc": document_id},
        )
    }
    rows: list[dict[str, object]] = []
    for page in pages:
        found = model_references(page["refs"])
        for chunk_id in _json_list(page["chunks"]):
            found.extend(_text_references(metadata.get(str(chunk_id))))
        seen: set[tuple[str, str]] = set()
        for name, section, relation in found:
            key = reference_name_key(name)
            if key is None or (key, section) in seen:
                continue
            seen.add((key, section))
            rows.append(
                {
                    "page": page["page_id"],
                    "ordinal": len(seen),
                    "doc": document_id,
                    "unit": page["unit_id"],
                    "name": name[:400],
                    "key": key[:400],
                    "section": section[:400] or None,
                    "relation": relation,
                }
            )
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO rag_wiki_references
                (page_id,ordinal,document_id,unit_id,target_name,target_key,target_section,relation)
                VALUES (:page,:ordinal,:doc,:unit,:name,:key,:section,:relation)""",
                rows,
            )


def backfill_references(connection: Any) -> None:
    """公開済みの文書の参照を文書ごとに作る（決定論の参照はすぐ使える）。

    中断の後は、済んだ文書を飛ばす。
    """
    documents = _fetch_all(
        connection,
        """SELECT document_id,published_fingerprint FROM rag_wiki_documents
        WHERE status='READY' ORDER BY document_id""",
        {},
    )
    for doc in documents:
        done = _fetch_one(
            connection,
            """SELECT 1 AS present FROM rag_wiki_references r
            JOIN rag_wiki_pages p ON p.page_id=r.page_id
            WHERE r.document_id=:doc AND p.source_fingerprint=:version FETCH FIRST 1 ROW ONLY""",
            {"doc": doc["document_id"], "version": doc["published_fingerprint"]},
        )
        if not done:
            index_references(connection, str(doc["document_id"]), str(doc["published_fingerprint"]))
        connection.commit()


# 題名・見出しの部分一致（いちばん弱い当たり方）で当てる文書の上限。
# 超える名前は広すぎるので解決しない（#5795）。
GENERIC_NAME_LIMIT = 4


class DocumentNames:
    """範囲の文書の名前の照合（回答の参照の補充の規則に、題名の部分一致を最後に足す。#5795）。"""

    def __init__(self, catalog: Sequence[Mapping[str, Any]], headings: Mapping[str, list[str]]):
        self.files = {str(row["document_id"]): str(row.get("title") or "") for row in catalog}
        self.headings = {did: headings.get(did, []) for did in self.files}
        self.name_keys = {
            did: document_name_keys(file_name, self.headings[did])
            for did, file_name in self.files.items()
        }
        # 逆引きの比較形も文書ごとに 1 回だけ作る。照合のたびに作り直すと、参照の数 × 文書の数だけ
        # 名前を正規化する（参照が集まる文書の単位で、関係の読み取りが 10 秒を超えた。#5966）。
        self.reference_keys = {
            did: document_reference_keys(file_name, self.headings[did])
            for did, file_name in self.files.items()
        }

    def resolve(self, name: str, *, exclude: str) -> list[str]:
        """名前が指す範囲の文書（いちばん確かな当たり方の文書。自分の文書は除く）。"""
        key = reference_name_key(name)
        return self.resolve_key(key, exclude=exclude) if key else []

    def resolve_key(self, key: str, *, exclude: str) -> list[str]:
        """比較形（``reference_name_key``）が指す範囲の文書。

        ファイル名の形はファイル名の完全一致だけ。それ以外は、別名の完全一致 →
        ファイル名が名前を含む → 題名・見出しの別名が名前を含む（文書の種類の呼び名・通称。
        ``GENERIC_NAME_LIMIT`` 以下の文書に当たるときだけ）の順で、いちばん確かな当たり方の
        文書を返す。形式の照合だけで、意味は判定しない。
        """
        others = [did for did in self.files if did != exclude]
        if key.startswith("file:"):
            return sorted(did for did in others if key in self.keys(did))
        ranks = {
            did: rank
            for did in others
            if (rank := document_name_rank(key, self.name_keys[did])) is not None
        }
        if ranks:
            best = min(ranks.values())
            return sorted(did for did, rank in ranks.items() if rank == best)
        if len(key) < 2:
            return []
        hits = sorted(
            did for did in others if any(key in alias for alias in self.name_keys[did][0])
        )
        return hits if len(hits) <= GENERIC_NAME_LIMIT else []

    def keys(self, document_id: str) -> frozenset[str]:
        """この文書を指す参照の比較形の完全一致の候補（逆引き）。"""
        return self.reference_keys.get(document_id, frozenset())

    def aliases(self, document_id: str) -> frozenset[str]:
        """この文書の題名・見出しの別名（逆引きで、名前を含む別名を探す）。"""
        keys = self.name_keys.get(document_id)
        return keys[0] if keys else frozenset()


class ReferenceTargets:
    """原文順の参照先候補。図と回答で同じ章節の単位を使う。"""

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.by_document: dict[str, list[tuple[str, list[str]]]] = {}
        self.resolved: dict[tuple[str, str], str | None] = {}
        # 文書ごとの、単位の章節の見出しの比較形。最初に照合するときに 1 回だけ作る。
        self.parts: dict[str, list[tuple[str, list[str]]]] = {}
        self.extend(sorted({str(row["document_id"]) for row in rows}), rows)

    def extend(self, documents: Sequence[str], rows: Sequence[dict[str, Any]]) -> None:
        """読んだ文書の候補を足す（候補の無い文書も、読んだ文書として残す）。

        既にある文書は足さない（同時の要求が同じ文書を読んでも、候補を重ねない）。
        """
        fresh = {doc for doc in documents if doc not in self.by_document}
        for doc in fresh:
            self.by_document[doc] = []
        for row in rows:
            if (doc := str(row["document_id"])) in fresh:
                self.by_document[doc].append((str(row["unit_id"]), _json_list(row["target_path"])))

    def resolve(self, doc: str, section: str) -> str | None:
        key = (doc, section)
        if key not in self.resolved:
            wanted = title_key(section)
            # 表記ごとに見出しを正規化し直すと、参照の数 × 文書の単位の数だけ正規化する（#5966）。
            if doc not in self.parts:
                self.parts[doc] = [
                    (unit, [title_key(part) for part in path])
                    for unit, path in self.by_document.get(doc, [])
                ]
            first = min(
                self.parts[doc], key=lambda item: _parts_rank(item[1], wanted), default=None
            )
            self.resolved[key] = first[0] if first else None
        return self.resolved[key]


def _section_rank(path: Sequence[str], wanted: str) -> int:
    return _parts_rank([title_key(part) for part in path], wanted)


def _parts_rank(parts: Sequence[str], wanted: str) -> int:
    """見出しの比較形（``title_key``）への表記の当たり方（完全一致 0・含む 1・無し 2）。"""
    if len(wanted) < 2:
        return 2
    if parts and parts[-1] == wanted:
        return 0
    return 1 if any(wanted in part for part in parts) else 2


def section_pages(pages: Sequence[Any], section: str) -> list[Any]:
    """章節の表記に当たる案内を先に並べる（見出しの完全一致 → 見出しが表記を含む → 残り）。

    表記が無い・短いときは並びを変えない。形式の照合だけで、意味は判定しない。
    """
    wanted = title_key(section)
    if len(wanted) < 2:
        return list(pages)

    def rank(page: Any) -> int:
        return _section_rank(page.unit.section_path, wanted)

    ranked = sorted(pages, key=rank)
    best = rank(ranked[0]) if ranked else 2
    return [page for page in ranked if rank(page) == best] if best < 2 else ranked
