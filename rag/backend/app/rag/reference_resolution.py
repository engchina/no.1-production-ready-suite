"""画面と検索が共有する、版と範囲を固定した明示参照の解決。I/Oは呼び手が担う。"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import cached_property
from typing import Literal

from app.rag.cross_references import (
    ReferenceSpec,
    SectionIndex,
    document_name_keys,
    document_name_rank,
    same_document_title,
    split_section_path,
    title_heading,
    title_key,
)


def document_title_from_paths(section_paths: Iterable[str]) -> str | None:
    """文書の題名。見出しの過半の先頭の段で、章・条などの番号が無いもの。無ければ None。"""
    heads = Counter(parts[0] for path in section_paths if (parts := split_section_path(path)))
    if not heads:
        return None
    head, count = heads.most_common(1)[0]
    return head if count * 2 > sum(heads.values()) and title_heading(head) else None


@dataclass(frozen=True)
class ReferenceDocument:
    """権限と明示範囲で絞った一つの成果物。異なる版を混ぜない。

    ``title`` は本文の題名。ファイル名（番号の接頭辞を含むことがある）と別に照合に使う。
    """

    document_id: str
    chunk_set_id: str | None
    name: str
    section_paths: tuple[str, ...]
    title: str | None = None

    # 参照の数だけ全文書の見出しを解析し直さないよう、同じsnapshotの解析結果を使い回す。
    @cached_property
    def name_keys(self) -> tuple[frozenset[str], str]:
        return document_name_keys(self.name, (self.title,) if self.title else self.section_paths)

    @cached_property
    def section_index(self) -> SectionIndex:
        return SectionIndex(self.section_paths)


@dataclass(frozen=True)
class ReferenceLocation:
    document_id: str
    chunk_set_id: str
    section_path: str | None


@dataclass(frozen=True)
class ReferenceResolution:
    status: Literal["resolved", "not_found", "ambiguous", "version_unconfirmed"]
    target: ReferenceLocation | None = None


def resolve_scoped_reference(
    spec: ReferenceSpec,
    *,
    source: ReferenceDocument,
    citing_path: str,
    documents: tuple[ReferenceDocument, ...],
) -> ReferenceResolution:
    """許可されたsnapshotだけで解決する。候補名や本文を未解決結果へ漏らさない。"""
    from app.rag.reference_grammar import current_grammar

    if not source.chunk_set_id:
        return ReferenceResolution("version_unconfirmed")

    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", source.name)
    # 略称は取込の参照の解決（``_citing_title``）と同じく、本文の題名を優先して展開する。
    if spec.document_title and (
        expanded := current_grammar().vocabulary.abbreviation_title(
            spec.document_title, source.title or stem
        )
    ):
        spec = replace(spec, document_title=expanded)
    if (
        not spec.document_title
        or same_document_title(spec.document_title, stem)
        or (source.title and same_document_title(spec.document_title, source.title))
    ):
        candidates = [source]
    else:
        key = title_key(spec.document_title)
        matches = [
            (score, item)
            for item in documents
            if item.document_id != source.document_id
            and (score := document_name_rank(key, item.name_keys)) is not None
        ]
        best = min((score for score, _item in matches), default=None)
        candidates = [item for score, item in matches if score == best]
    if not candidates:
        return ReferenceResolution("not_found")
    if len({item.document_id for item in candidates}) != 1:
        return ReferenceResolution("ambiguous")
    versions = {item.chunk_set_id for item in candidates}
    if None in versions or "" in versions or len(versions) != 1:
        return ReferenceResolution("version_unconfirmed")
    document = candidates[0]
    if spec.kind == "document":
        paths: list[str | None] = [None]
    else:
        paths = list(
            document.section_index.resolve_candidates(
                spec,
                citing_path=(
                    split_section_path(citing_path)
                    if document.document_id == source.document_id
                    else ()
                ),
            )
        )
    if not paths:
        return ReferenceResolution("not_found")
    if len(paths) != 1:
        return ReferenceResolution("ambiguous")
    return ReferenceResolution(
        "resolved",
        ReferenceLocation(document.document_id, str(document.chunk_set_id), paths[0]),
    )
