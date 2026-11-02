"""明示参照を版付きの記録にする。未解決も残し、画面と検索で同じ解決規則を使う。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.rag.cross_references import extract_references, reference_targets
from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference


@dataclass(frozen=True)
class GraphReferenceRecord:
    reference_id: str
    knowledge_base_id: str
    source_document_id: str
    source_chunk_set_id: str
    source_chunk_id: str
    source_section_path: str
    label: str
    kind: str
    key: str
    document_title: str | None
    status: str
    target_document_id: str | None
    target_chunk_set_id: str | None
    target_section_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_reference_records(
    *,
    knowledge_base_id: str,
    source: ReferenceDocument,
    chunks: Sequence[Any],
    documents: tuple[ReferenceDocument, ...],
) -> list[GraphReferenceRecord]:
    """同文書・別文書の参照表記を同一snapshot内で解決する。外部APIは呼ばない。"""
    from rag_parser_core.extraction import is_derived_content

    from app.rag.cross_references import SECTION_PATH_SEPARATOR, split_section_path

    records: dict[str, GraphReferenceRecord] = {}
    for chunk in chunks:
        if is_derived_content(chunk.metadata.get("content_kind"), chunk.metadata):
            continue
        path = SECTION_PATH_SEPARATOR.join(split_section_path(chunk.metadata.get("section_path")))
        specs = extract_references(chunk.text) or [
            target.spec for target in reference_targets(chunk.metadata)
        ]
        chunk_id = f"{source.document_id}:{source.chunk_set_id}:{chunk.index}"
        for spec in specs:
            resolution = resolve_scoped_reference(
                spec, source=source, citing_path=path, documents=documents
            )
            identity = "|".join(
                (knowledge_base_id, chunk_id, spec.kind, spec.key, spec.document_title or "")
            )
            reference_id = hashlib.sha256(identity.encode()).hexdigest()
            target = resolution.target
            records[reference_id] = GraphReferenceRecord(
                reference_id=reference_id,
                knowledge_base_id=knowledge_base_id,
                source_document_id=source.document_id,
                source_chunk_set_id=source.chunk_set_id or "",
                source_chunk_id=chunk_id,
                source_section_path=path,
                label=spec.label,
                kind=spec.kind,
                key=spec.key,
                document_title=spec.document_title,
                status=resolution.status,
                target_document_id=target.document_id if target else None,
                target_chunk_set_id=target.chunk_set_id if target else None,
                target_section_path=target.section_path if target else None,
            )
    return list(records.values())
