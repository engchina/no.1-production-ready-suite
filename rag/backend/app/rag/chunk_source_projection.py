"""既存の型付き画像位置を、同時に保存する抽出の出所へ照合する。"""

from collections import defaultdict
from collections.abc import Sequence

from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text_map import (
    VISION_CHUNK_MAP_KEY,
    VisionTextSource,
    chunk_projected_text,
    projected_element_text,
)
from rag_pipeline_core.chunking import Chunk, pending_table_supplement_source

_ERROR = "保存する抽出と画像本文の出所が一致しません。解析結果と分割結果を確認してください。"
type _SourceIdentity = tuple[str, str, str, str, str]


def _source_identity(source: VisionTextSource) -> _SourceIdentity:
    if (
        not isinstance(source.element_id, str)
        or not source.element_id
        or not isinstance(source.record_id, str)
        or not source.record_id
        or not isinstance(source.element_body_sha256, str)
        or type(source.occurrence) is not int
        or source.occurrence < 0
    ):
        raise ValueError(_ERROR)
    # occurrenceは分割前処理で変わる局所番号で、原文の全局位置として照合しない。
    return (
        source.element_id,
        source.record_id,
        source.element_body_sha256,
        source.role,
        source.projection.dumps(),
    )


def _validate_chunk_source_projection(
    extraction: StructuredExtraction, chunks: Sequence[Chunk]
) -> None:
    """画像mapを宣言するchunkだけ検証し、入力と既存のnative経路を変更しない。"""
    declared: set[_SourceIdentity] = set()
    for chunk in chunks:
        if VISION_CHUNK_MAP_KEY not in chunk.metadata:
            continue
        projection = chunk_projected_text(chunk.text, chunk.metadata)
        if not projection.ranges:
            raise ValueError(_ERROR)
        for row in projection.ranges:
            if type(row.start) is not int or type(row.end) is not int:
                raise ValueError(_ERROR)
            declared.add(_source_identity(row.source))
    if not declared:
        return
    referenced = {source[0] for source in declared}
    owners: dict[str, list[DocumentElement]] = defaultdict(list)
    for element in extraction.elements:
        if element.element_id in referenced:
            owners[element.element_id].append(element)
    allowed: set[_SourceIdentity] = set()
    for element_id in referenced:
        if len(owners[element_id]) != 1:
            raise ValueError(_ERROR)
        element = owners[element_id][0]
        projection = projected_element_text(element_id, element.text, element.metadata)
        allowed.update(_source_identity(row.source) for row in projection.ranges)
        if (source := pending_table_supplement_source(element)) is not None:
            allowed.add(_source_identity(source))
    if not declared <= allowed:
        raise ValueError(_ERROR)
