"""活動版の chunk 座標を、保存済み parser 原文の完全な段落に照合する。"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pr_backend_core.source_text import prose_boundaries
from rag_parser_core.extraction import StructuredExtraction, is_derived_content
from rag_parser_core.xml_tree_json import SourceParagraph, source_paragraphs

from app.rag.wiki_models import WikiUnit, fingerprint
from app.rag.wiki_packet import SourceContext
from app.rag.wiki_paragraphs import Paragraph


@dataclass
class _Window:
    start: int
    end: int
    targets: list[tuple[str, int, int]] = field(default_factory=list)


def bind_paragraphs(
    units: Sequence[WikiUnit],
    extraction: StructuredExtraction,
    offsets: Mapping[str, tuple[int, int]],
) -> dict[str, SourceContext]:
    text = extraction.raw_text
    paragraphs = source_paragraphs(extraction, text)
    if (
        paragraphs is None
        and extraction.elements
        and not extraction.tables
        and all(
            e.kind == "text"
            and e.content_kind in (None, "text")
            and not is_derived_content(e.content_kind, e.metadata)
            for e in extraction.elements
        )
    ):
        # 固定長の子からではなく、保存された連続する本文の改段落と文末を使う。
        paragraphs = tuple(
            SourceParagraph(r.start, r.end, ends) for r, ends in prose_boundaries(text)
        )
    if not paragraphs:
        return {}
    starts, ends = [p.start for p in paragraphs], [p.end for p in paragraphs]
    elements: list[tuple[int, int, tuple[str, ...]]] = []
    cursor = 0
    for e in extraction.elements:
        if not e.text or is_derived_content(e.content_kind, e.metadata):
            continue
        pos = text.find(e.text, cursor)
        if pos < 0:
            return {}
        cursor = pos + len(e.text)
        elements.append((pos, cursor, tuple(e.section_path)))
    element_starts = [e[0] for e in elements]
    element_ends = [e[1] for e in elements]
    result = {}
    for unit in units:
        windows: list[_Window] = []
        paths: list[tuple[str, ...]] = []
        for cid in unit.source_chunk_ids:
            if cid not in offsets:
                break
            a, b = offsets[cid]
            expected = unit.source_chunk_hashes.get(cid, unit.source_hash)
            if not 0 <= a < b <= len(text) or fingerprint(text[a:b]) != expected:
                break
            lo, hi = bisect_right(ends, a), bisect_left(starts, b)
            begin = min(a, starts[lo]) if lo < hi else a
            end = max(b, ends[hi - 1]) if lo < hi else b
            # 同じ親の子が同じ段落を参照するとき、段落は一度だけ載せ、各子の範囲を残す。
            if (
                windows
                and windows[-1].start <= begin <= windows[-1].end
                and a >= windows[-1].targets[-1][1]
            ):
                windows[-1].end = max(windows[-1].end, end)
            else:
                windows.append(_Window(begin, end))
            windows[-1].targets.append((cid, a, b))
            paths.extend(
                e[2]
                for e in elements[bisect_right(element_ends, a) : bisect_left(element_starts, b)]
            )
        else:
            pieces: list[str] = []
            ranges: list[tuple[int, int]] = []
            segments: list[Paragraph] = []
            pos = 0
            for i, w in enumerate(windows):
                pieces.append(text[w.start : w.end])
                ranges.extend((pos + a - w.start, pos + b - w.start) for _, a, b in w.targets)
                inside = paragraphs[bisect_right(ends, w.start) : bisect_left(starts, w.end)]
                separator = 2 if i + 1 < len(windows) else 0
                anchor = w.targets[0][0]
                for index, p in enumerate(inside):
                    left = w.start if index == 0 else p.start
                    right = inside[index + 1].start if index + 1 < len(inside) else w.end
                    local_end = (
                        pos + right - w.start + (separator if index + 1 == len(inside) else 0)
                    )
                    segments.append(
                        Paragraph(
                            pos + left - w.start,
                            local_end,
                            tuple(
                                pos + pt - w.start for pt in p.sentence_ends if left < pt < right
                            ),
                            anchor,
                        )
                    )
                pos_next = pos + w.end - w.start + separator
                if not inside:
                    segments.append(Paragraph(pos, pos_next, source=anchor))
                pos = pos_next
            path = list(unit.section_path)
            if not path:
                for level in zip(*paths, strict=False):
                    if len(set(level)) != 1:
                        break
                    path.append(level[0])
            source = SourceContext(
                unit.document_id,
                unit.chunk_set_id,
                unit.source_hash,
                "\n\n".join(pieces),
                tuple(path),
                tuple(ranges),
                tuple(segments),
            )
            source.verify(unit)
            result[unit.unit_id] = source
    return result
