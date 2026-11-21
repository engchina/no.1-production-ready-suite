"""認可済みの対象・解析の保存版から、Wiki の原文窓と参照候補を組み立てる。"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from itertools import groupby

from pydantic import ValidationError
from rag_parser_core.extraction import StructuredExtraction

from app.clients.oracle import OracleClient
from app.rag.cross_references import (
    SectionIndex,
    reference_targets,
    resolve_reference_specs,
    split_section_path,
)
from app.rag.wiki_models import WikiUnit
from app.rag.wiki_packet import SourceContext
from app.rag.wiki_source_context import bind_paragraphs


async def source_inputs(
    oracle: OracleClient,
    units: Sequence[WikiUnit],
    metadata: Mapping[str, Mapping[str, object]],
    authorize: Callable[[], Awaitable[None]],
) -> tuple[dict[str, SourceContext], dict[str, list[str]]]:
    sources: dict[str, SourceContext] = {}
    related: dict[str, list[str]] = {}
    if not units:
        return sources, related
    doc = units[0].document_id
    if any(u.document_id != doc for u in units):
        raise ValueError("同じ文書の原文単位だけを対象にしてください。")
    await authorize()
    recipes = await oracle.chunk_set_extraction_recipe_ids([u.chunk_set_id for u in units])
    for version, rows in groupby(units, lambda u: u.chunk_set_id):
        block = list(rows)
        recipe = recipes.get(version)
        parts: dict[str, tuple[int, int]] = {}
        for u in block:
            for cid in u.source_chunk_ids:
                m = metadata.get(cid, {})
                a, b = m.get("start_offset"), m.get("end_offset")
                if type(a) is int and type(b) is int and a < b:
                    parts[cid] = (a, b)
        if recipe and parts:
            await authorize()
            record = await oracle.get_document_extraction_artifact(
                document_id=doc, extraction_recipe_id=recipe
            )
            if (
                record
                and record.get("document_id") == doc
                and record.get("extraction_recipe_id") == recipe
            ):
                try:
                    parsed = StructuredExtraction.model_validate(record.get("extraction_json"))
                except ValidationError:
                    pass
                else:
                    sources.update(bind_paragraphs(block, parsed, parts))
        paths = {
            u.unit_id: sources[u.unit_id].path if u.unit_id in sources else tuple(u.section_path)
            for u in block
        }
        lookup = SectionIndex(list(path) for path in paths.values() if path)
        first: dict[tuple[str, ...], str] = {}
        for uid, path in paths.items():
            for n in range(1, len(path) + 1):
                first.setdefault(path[:n], uid)
        for unit in block:
            path = paths[unit.unit_id]
            if not path:
                continue
            dests: dict[str, None] = {}
            for i, cid in enumerate(unit.source_chunk_ids):
                original = unit.text
                if len(unit.source_chunk_ids) > 1:
                    context = sources.get(unit.unit_id)
                    if context is None or len(context.ranges) != len(unit.source_chunk_ids):
                        continue
                    start, end = context.ranges[i]
                    original = context.text[start:end]
                for ref in reference_targets(metadata.get(cid, {})):
                    if not ref.label or ref.label not in original:
                        continue
                    matches = resolve_reference_specs(
                        [ref.spec], lookup, citing_path=path, document_title=path[0]
                    )
                    if len(matches) == 1 and matches[0].resolved:
                        dest = first.get(split_section_path(matches[0].section_path))
                        if dest is not None and dest != unit.unit_id:
                            dests[dest] = None
            if dests:
                related[unit.unit_id] = list(dests)
    await authorize()
    return sources, related
