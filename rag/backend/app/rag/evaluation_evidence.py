"""保存済みの回答で採用した引用を、評価 template の根拠の書式で出す。"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.evaluation_excel import document_format, parse_evidence_line


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        return _positive_int(int(value))
    return None


def _range(start: object, end: object) -> str | None:
    first = _positive_int(start)
    last = _positive_int(end)
    if first is None:
        return None
    return f"{first}-{last}" if last is not None and last > first else str(first)


def used_evidence_lines(citations: Sequence[Mapping[str, Any]]) -> str:
    """採用の印が無い旧記録・親だけの候補は出さず、同じ位置の引用は重ねない。

    回答の本文や検索候補の順から採用を推測しない。番号・位置は保存された引用だけを使う。
    """
    lines: list[str] = []
    for citation in citations:
        meta = citation.get("metadata")
        if not isinstance(meta, Mapping) or meta.get("evidence_model_used") is not True:
            continue
        # 親の参照に付いた候補の位置を、確定した子の引用として出さない。
        if meta.get("answer_citation_candidate_lines") and not (
            meta.get("answer_citation_lines") and meta.get("answer_citation_provenance") == "child"
        ):
            continue
        name = str(
            citation.get("file_name") or meta.get("file_name") or citation.get("source") or ""
        ).strip()
        document_id = str(citation.get("document_id") or "").strip()
        if not name and not document_id:
            continue
        parts = [name or f"文書ID: {document_id}"]
        pages = _range(
            meta.get("page_start") or meta.get("page_number") or meta.get("page"),
            meta.get("page_end"),
        )
        kind = document_format(name)
        # template の位置の契約に合わせ、表計算の内部の頁番号と PDF の表の行を混同しない。
        if pages and kind not in {"sheet", "text"}:
            parts.append(f"スライド: {pages}" if kind == "slides" else f"p.{pages}")
        if kind in {"sheet", "unknown"}:
            if sheet := str(meta.get("sheet_name") or "").strip():
                parts.append(f"シート: {sheet}")
            if rows := _range(meta.get("row_start"), meta.get("row_end")):
                parts.append(f"行: {rows}")
        section = meta.get("section_path") or meta.get("section_title")
        if isinstance(section, list):
            section = " > ".join(str(item) for item in section if item)
        if section:
            parts.append(f"章節: {section}")
        line = " | ".join(parts)
        if isinstance(parse_evidence_line(line), str):
            continue
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)
