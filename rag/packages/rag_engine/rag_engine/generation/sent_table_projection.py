"""実際に送った原文の Markdown の表を、原文の位置へ投影する（#4349）。

投影するのは送達した引用単位の目録（``SentUnitCatalog``）の版の本文だけで、保存済みの表や別の版を推定しない。
行・セルは原文の文字位置（``text[start:end] == raw``）と引用単位の ID を持つ。列の見出しは記録しない
（``not_recorded``。推測しない）。値・条件の意味は扱わない。

表の 1 行は 1 つの物理行で、1 つの引用単位に収まるものだけを扱う。列の数がそろわない、code span を含むなど、
この簡易な分け方で一意に決まらない snapshot は投影せず、理由を返す（呼出し側はその表を扱わない）。
"""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from typing import Any

from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog

CONTRACT = "derived_from_sent_markdown_ranges_v1"
HEADER_NOT_RECORDED = "not_recorded"


class ProjectionError(ValueError):
    """表の投影が一意に決まらない（理由の code を持つ）。"""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _decode(raw: str) -> str:
    """Markdown のセルの escape（``\\|``・``\\\\``）だけを戻す。"""
    result = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and index + 1 < len(raw) and raw[index + 1] in ("|", "\\"):
            result.append(raw[index + 1])
            index += 2
        else:
            result.append(raw[index])
            index += 1
    return "".join(result)


def tokenize_line(text: str, start: int, end: int) -> list[tuple[int, int, str, str]]:
    """1 つの物理行を、セルの (開始, 終了, 原文, escape を戻した文字) に分ける。"""
    line = text[start:end]
    left = start + len(line) - len(line.lstrip())
    right = end - (len(line) - len(line.rstrip()))
    if left >= right or text[left] != "|" or text[right - 1] != "|":
        raise ProjectionError("markdown_row_not_supported")
    cuts = []
    index = left
    while index < right:
        if text[index] == "\\" and index + 1 < right and text[index + 1] in ("|", "\\"):
            index += 2
            continue
        if text[index] == "|":
            cuts.append(index)
        index += 1
    if not cuts or cuts[0] != left or cuts[-1] != right - 1:
        raise ProjectionError("markdown_row_not_supported")
    cells = []
    for a, b in pairwise(cuts):
        raw = text[a + 1 : b]
        cell_start = a + 1 + len(raw) - len(raw.lstrip())
        cell_end = b - (len(raw) - len(raw.rstrip()))
        if not raw.strip():
            cell_start = cell_end = b
        cells.append((cell_start, cell_end, text[cell_start:cell_end], _decode(text[cell_start:cell_end])))
    return cells


def is_separator(cells: list[tuple[int, int, str, str]]) -> bool:
    """Markdown の見出しの区切りの行（``|---|:--:|``）か。"""
    return bool(cells) and all(len(value.strip(":")) >= 3 and set(value) <= set("-:") for _, _, _, value in cells)


def _block(pending: list[dict[str, Any]], snapshot: EvidenceUnitSnapshot, span: dict[str, Any], text: str) -> dict[str, Any]:
    if len({len(row["cells"]) for row in pending}) != 1:
        raise ProjectionError("ambiguous_column_count")
    start, end = pending[0]["start"], pending[-1]["end"]
    block_id = "MB" + _digest([CONTRACT, snapshot.snapshot_id, span.get("evidence_id"), start, end])
    rows = []
    for row_index, row in enumerate(pending):
        row_id = "MR" + _digest([block_id, row_index, row["start"], row["end"]])
        cells = [
            {
                "cell_id": "MC" + _digest([row_id, column, a, b]),
                "unit_id": row["unit_id"],
                "row_index": row_index,
                "column_index": column,
                "start": a,
                "end": b,
                "raw": raw,
                "text": value,
                "header": HEADER_NOT_RECORDED,
            }
            for column, (a, b, raw, value) in enumerate(row["cells"])
        ]
        rows.append(
            {**{key: value for key, value in row.items() if key != "cells"}, "row_id": row_id, "row_index": row_index, "cells": cells}
        )
    return {
        "block_id": block_id,
        "start": start,
        "end": end,
        "raw": text[start:end],
        "origin": "derived_from_sent_markdown",
        "header": HEADER_NOT_RECORDED,
        "rows": rows,
    }


def project_snapshot(catalog: SentUnitCatalog, snapshot: EvidenceUnitSnapshot) -> dict[str, Any]:
    """1 つの送達 snapshot の表のブロック。一意に決まらなければ ``ProjectionError``。"""
    if EvidenceUnitSnapshot.create(snapshot.span(), snapshot.units, snapshot.contract) != snapshot:
        raise ProjectionError("snapshot_identity_mismatch")
    span = snapshot.span()
    text = str(span.get("text") or "")
    blocks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    offset = 0
    # 物理行の位置は splitlines の累積長だけから得る（find や意味による分け方はしない）。
    for physical in text.splitlines(keepends=True):
        end = offset + len(physical.rstrip("\r\n"))
        line = text[offset:end]
        if line.strip().startswith("|") and line.strip().endswith("|"):
            # code span の中の縦線の別の解釈を、この簡易な分け方で確定しない。
            if "`" in line:
                raise ProjectionError("code_syntax_not_supported")
            cells = tokenize_line(text, offset, end)
            containing = [uid for (a, b), uid in zip(snapshot.units, snapshot.unit_ids, strict=True) if a <= offset and end <= b]
            if len(containing) != 1:
                raise ProjectionError("unit_not_containing_row")
            bound = catalog.bind(containing)
            if len(bound) != 1 or bound[0].snapshot != snapshot:
                raise ProjectionError("sent_identity_mismatch")
            pending.append(
                {
                    "start": offset,
                    "end": end,
                    "raw": line,
                    "unit_id": containing[0],
                    "syntax": "markdown_separator" if is_separator(cells) else "pipe_row",
                    "cells": cells,
                }
            )
        elif pending:
            blocks.append(_block(pending, snapshot, span, text))
            pending = []
        offset += len(physical)
    if pending:
        blocks.append(_block(pending, snapshot, span, text))
    return {"snapshot_id": snapshot.snapshot_id, "span": span, "blocks": blocks}


def table_view(projected: dict[str, Any]) -> dict[str, Any]:
    """1 つの表のブロックに、呼出しの中だけの別名 ID（R1・R1C1）を付ける。区切りの行は行として数えない。"""
    block = projected["blocks"][0]
    span = projected["span"]
    text = str(span.get("text") or "")
    rows = []
    aliases: dict[str, dict[str, Any]] = {}
    for row in block["rows"]:
        if row["syntax"] == "markdown_separator":
            continue  # 区切りは構文。行として判定させない（原文には残る）。
        rid = f"R{len(rows) + 1}"
        cells = []
        for cell in row["cells"]:
            cid = f"{rid}C{cell['column_index'] + 1}"
            if text[cell["start"] : cell["end"]] != cell["raw"]:
                raise ProjectionError("cell_range_mismatch")
            aliases[cid] = {
                "kind": "cell",
                "row": rid,
                "projection_id": cell["cell_id"],
                "start": cell["start"],
                "end": cell["end"],
                "raw": cell["raw"],
                "text": cell["text"],
                "header": cell["header"],
                "unit_id": cell["unit_id"],
            }
            cells.append({"cell_id": cid, "text": cell["text"]})
        if text[row["start"] : row["end"]] != row["raw"]:
            raise ProjectionError("row_range_mismatch")
        aliases[rid] = {
            "kind": "row",
            "projection_id": row["row_id"],
            "start": row["start"],
            "end": row["end"],
            "raw": row["raw"],
            "unit_id": row["unit_id"],
        }
        rows.append({"row_id": rid, "cells": cells})
    return {
        "evidence_id": str(span.get("evidence_id") or ""),
        "source": str(span.get("source") or ""),
        "section_path": [str(part) for part in span.get("section_path") or []],
        "snapshot_id": projected["snapshot_id"],
        "block_projection_id": block["block_id"],
        "header": block["header"],
        "before_text": text[: block["start"]].strip(),
        "after_text": text[block["end"] :].strip(),
        "rows": rows,
        "aliases": aliases,
    }


def sent_tables(catalog: SentUnitCatalog) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """送達した snapshot のうち、表がちょうど 1 つのものの表の view と、扱わない snapshot の理由。

    表の無い snapshot は理由に含めない。表が 2 つ以上・投影が一意に決まらない snapshot は理由だけを返す。
    """
    if not isinstance(catalog, SentUnitCatalog):
        raise ProjectionError("missing_sent_catalog")
    tables: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for snapshot in catalog.snapshots:
        evidence_id = str(snapshot.span().get("evidence_id") or "")
        if snapshot.snapshot_id in seen:
            skipped.append({"evidence_id": evidence_id, "reason": "ambiguous_repeated_snapshot"})
            continue
        seen.add(snapshot.snapshot_id)
        try:
            projected = project_snapshot(catalog, snapshot)
            if not projected["blocks"]:
                continue
            if len(projected["blocks"]) != 1:
                raise ProjectionError("exactly_one_table_block_required")
            view = table_view(projected)
        except ProjectionError as exc:
            skipped.append({"evidence_id": evidence_id, "reason": str(exc)})
            continue
        if view["rows"]:
            tables.append(view)
    return tables, skipped
