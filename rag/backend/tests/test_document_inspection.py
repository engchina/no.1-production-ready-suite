"""大きな合成文書の取得範囲と、完全エクスポートとの独立を検証する。"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.document_inspection import extraction_window, text_window


def fixture_payload() -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {
            "element_id": f"node-{i}",
            "page_number": i // 10 + 1,
            "text": f"項目{i} " + "本文" * 100,
            "order": i,
        }
        for i in range(1332)
    ]
    return {
        "raw_text": "\n".join(e["text"] for e in elements),
        "elements": elements,
        "pages": [],
        "parser_artifacts": {"large_unused": "x" * 1_000_000},
    }


def test_long_single_line_is_read_in_finite_windows_without_losing_original() -> None:
    raw = json.dumps({"nodes": ["合成テキスト" * 100 for _ in range(2000)]}, ensure_ascii=False)
    windows = [text_window(raw, i, 8192) for i in range(0, len(raw), 8192)]
    assert all(len(row["text"]) <= 8192 for row in windows)
    assert "".join(row["text"] for row in windows) == raw
    assert len(raw) > 1_000_000


def test_citation_focus_can_read_target_beyond_initial_window() -> None:
    raw = "先頭" * 100000 + "対象の原文" + "末尾" * 10000
    result = text_window(raw, 0, 8192, "対象の原文")
    assert result["offset"] > 0
    assert "対象の原文" in result["text"]
    assert len(result["text"]) == 8192


def test_structured_window_is_bounded_and_can_reach_last_element() -> None:
    payload = fixture_payload()
    initial = extraction_window(payload, mode="structure", offset=0, limit=25)
    target = extraction_window(
        payload, mode="structure", offset=0, limit=25, element_id="node-1331"
    )
    assert initial["total"] == 1332
    assert len(initial["payload"]["elements"]) == 25
    assert initial["payload"]["raw_text"] == ""
    assert "large_unused" not in initial["payload"]["parser_artifacts"]
    assert target["offset"] == 1325
    assert target["payload"]["elements"][-1]["element_id"] == "node-1331"
    assert len(payload["elements"]) == 1332


def test_page_scope_is_applied_before_range_and_text_generation() -> None:
    payload = fixture_payload()
    result = extraction_window(payload, mode="structure", offset=0, limit=25, page=2)
    assert result["total"] == 10
    assert {row["page_number"] for row in result["payload"]["elements"]} == {2}
    text = extraction_window(payload, mode="text", offset=0, limit=25, page=2)
    assert "項目10 " in text["payload"]["raw_text"]
    assert "項目0 " not in text["payload"]["raw_text"]


def test_empty_success_is_distinct_from_missing_or_loading() -> None:
    result = extraction_window({}, mode="structure", offset=0, limit=25)
    assert result["total"] == 0
    assert result["payload"]["elements"] == []


def test_one_oversized_node_cannot_mount_unbounded_text() -> None:
    payload = {"elements": [{"element_id": "node-1", "text": "長文" * 100000}]}
    result = extraction_window(payload, mode="structure", offset=0, limit=25)
    assert len(result["payload"]["elements"][0]["text"]) == 8192
    assert result["payload"]["preview_truncated"]
    assert len(payload["elements"][0]["text"]) == 200000


def test_preview_cache_is_versioned_private_and_can_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import document_preview_cache as module

    cache = module.DocumentPreviewCache(tmp_path / "cache")
    cache.put("document:version1:page1:dpi144", b"page1")
    assert cache.get("document:version1:page1:dpi144") == b"page1"
    assert cache.get("document:version2:page1:dpi144") is None
    assert cache.get("document:version1:page2:dpi144") is None
    assert cache.path("document:version1:page1:dpi144").stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(module, "CACHE_TTL_SECONDS", -1)
    assert cache.get("document:version1:page1:dpi144") is None


def test_preview_cache_enforces_total_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import document_preview_cache as module

    monkeypatch.setattr(module, "MAX_CACHE_BYTES", 10)
    cache = module.DocumentPreviewCache(tmp_path)
    cache.put("a", b"123456")
    cache.put("b", b"abcdef")
    assert sum(p.stat().st_size for p in tmp_path.glob("*.cache")) <= 10
    assert cache.get("b") == b"abcdef"


def test_long_element_can_read_every_part_and_retains_edit_identity() -> None:
    text = "長い合成本文" * 20000
    payload = {"elements": [{"element_id": "node-long", "text": text}]}
    parts = [
        extraction_window(payload, mode="structure", offset=0, limit=25, text_offset=i)["payload"][
            "elements"
        ][0]
        for i in range(0, len(text), 8192)
    ]
    assert "".join(part["text"] for part in parts) == text
    assert {part["element_id"] for part in parts} == {"node-long"}
    assert len({part["metadata"]["preview_text_sha256"] for part in parts}) == 1


def test_table_cells_are_read_in_global_bounded_windows() -> None:
    payload = {
        "tables": [
            {"table_id": "t1", "cells": [{"row": i, "col": 0, "text": str(i)} for i in range(1000)]}
        ]
    }
    first = extraction_window(payload, mode="structure", offset=0, limit=25)
    later = extraction_window(payload, mode="structure", offset=0, limit=25, cell_offset=975)
    assert first["cell_total"] == 1000
    assert len(first["payload"]["tables"][0]["cells"]) == 25
    assert later["payload"]["tables"][0]["cells"][-1]["row"] == 999
