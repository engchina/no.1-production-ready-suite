"""画面と検索が共有する、版と範囲を固定した明示参照の解決。I/Oは呼び手が担う。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from app.rag.cross_references import (
    ReferenceSpec,
    SectionIndex,
    document_name_match,
    same_document_title,
    split_section_path,
)


@dataclass(frozen=True)
class ReferenceDocument:
    """権限と明示範囲で絞った一つの成果物。異なる版を混ぜない。"""

    document_id: str
    chunk_set_id: str | None
    name: str
    section_paths: tuple[str, ...]


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

    if spec.document_title and (
        expanded := current_grammar().vocabulary.abbreviation_title(
            spec.document_title, re.sub(r"\.[A-Za-z0-9]{1,5}$", "", source.name)
        )
    ):
        spec = replace(spec, document_title=expanded)
    if not spec.document_title or same_document_title(
        spec.document_title, re.sub(r"\.[A-Za-z0-9]{1,5}$", "", source.name)
    ):
        candidates = [source]
    else:
        matches = [
            (score, item)
            for item in documents
            if item.document_id != source.document_id
            and (score := document_name_match(spec.document_title, item.name, item.section_paths))
            is not None
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
            SectionIndex(document.section_paths).resolve_candidates(
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
