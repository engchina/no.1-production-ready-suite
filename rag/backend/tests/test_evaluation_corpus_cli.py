"""評価の合成の資料の取込 CLI（#1231）。API は httpx の MockTransport で置き換える。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.evaluation_corpus_cli import (
    CorpusError,
    CorpusLoader,
    main,
    referenced_files,
    resolve_golden_set,
)

GOLDEN_SET: dict[str, Any] = {
    "cases": [
        {"id": "a", "query": "q", "relevant_document_ids": ["file:manual.pdf", "doc-fixed"]},
        {"id": "b", "query": "q", "relevant_document_ids": ["file:params.xlsx", "file:manual.pdf"]},
        {"id": "c", "query": "q", "answerable": False},
    ]
}


def test_referenced_files_and_resolution() -> None:
    assert referenced_files(GOLDEN_SET) == ["manual.pdf", "params.xlsx"]
    resolved = resolve_golden_set(GOLDEN_SET, {"manual.pdf": "d1", "params.xlsx": "d2"}, "kb-1")
    assert resolved["cases"][0]["relevant_document_ids"] == ["d1", "doc-fixed"]
    assert resolved["cases"][1]["relevant_document_ids"] == ["d2", "d1"]
    assert resolved["knowledge_base_ids"] == ["kb-1"]
    # 元の評価セットは変えない。
    assert GOLDEN_SET["cases"][0]["relevant_document_ids"][0] == "file:manual.pdf"
    with pytest.raises(CorpusError):
        resolve_golden_set(GOLDEN_SET, {"manual.pdf": "d1"}, "kb-1")


class FakeApi:
    """取込の API。レシピは呼ばれるたびに次の状態へ進む（REVIEW では承認を待つ）。"""

    def __init__(self, statuses: dict[str, list[str]]) -> None:
        self.statuses = statuses
        self.calls: list[tuple[str, str, Any]] = []
        self.documents: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        body: Any = None
        if request.method in {"POST", "PUT"} and request.headers.get("content-type", "").startswith(
            "application/json"
        ):
            body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path, body))
        if path == "/knowledge-bases":
            return httpx.Response(200, json={"data": {"id": "kb-new"}})
        if path == "/documents/upload":
            name = "params.xlsx" if b'filename="params.xlsx"' in request.content else "manual.pdf"
            document_id = f"doc-{name}"
            self.documents[document_id] = name
            return httpx.Response(200, json={"data": {"document_id": document_id}})
        document_id = path.split("/")[2]
        name = self.documents[document_id]
        if path.endswith("/recipes") and request.method == "GET":
            queue = self.statuses[name]
            status = queue[0] if len(queue) == 1 else queue.pop(0)
            return httpx.Response(
                200, json={"data": [{"recipe_id": "r1", "status": status, "error_message": None}]}
            )
        return httpx.Response(200, json={"data": {}})


def _loader(api: FakeApi) -> CorpusLoader:
    client = httpx.Client(transport=httpx.MockTransport(api), base_url="http://test")
    return CorpusLoader(
        client, "http://test", poll_interval_seconds=0, sleep=lambda _: None, log=lambda _: None
    )


def test_loader_uploads_sets_excel_recipe_and_approves_gates(tmp_path: Path) -> None:
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "params.xlsx").write_bytes(b"PK")
    api = FakeApi(
        {
            # アップロード直後の取得 → 待ちの間に REVIEW（承認）→ INDEXED。
            "manual.pdf": ["UPLOADED", "REVIEW", "REVIEW", "INDEXED"],
            "params.xlsx": ["UPLOADED", "INGESTING", "INDEXED"],
        }
    )
    loader = _loader(api)
    knowledge_base_id = loader.create_knowledge_base("評価")
    documents = {
        name: loader.ingest(tmp_path / name, knowledge_base_id)
        for name in ("manual.pdf", "params.xlsx")
    }
    loader.wait_indexed(documents)

    assert documents == {"manual.pdf": "doc-manual.pdf", "params.xlsx": "doc-params.xlsx"}
    puts = [call for call in api.calls if call[0] == "PUT"]
    # Excel だけ前処理 excel_to_json のレシピにする。
    assert puts == [
        ("PUT", "/documents/doc-params.xlsx/recipes/r1", {"preprocess_profile": "excel_to_json"})
    ]
    jobs = [call[1] for call in api.calls if call[1].endswith("/ingestion-jobs")]
    assert len(jobs) == 2
    approvals = [call[1] for call in api.calls if call[1].endswith("/approve")]
    # 同じゲートは 1 回だけ承認する。
    assert approvals == ["/documents/doc-manual.pdf/recipes/r1/approve"]


def test_loader_reports_failed_ingestion(tmp_path: Path) -> None:
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4")
    api = FakeApi({"manual.pdf": ["UPLOADED", "ERROR"]})
    loader = _loader(api)
    document_id = loader.ingest(tmp_path / "manual.pdf", "kb")
    with pytest.raises(CorpusError, match="取込に失敗しました"):
        loader.wait_indexed({"manual.pdf": document_id})


def test_main_rejects_missing_corpus_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "set.json"
    golden.write_text(json.dumps(GOLDEN_SET), encoding="utf-8")
    code = main([str(golden), "--output", str(tmp_path / "out.json")])
    assert code == 2
    assert "資料が見つかりません" in capsys.readouterr().err


def test_required_evidence_file_references_are_collected_and_resolved() -> None:
    """必要な根拠の文書（#1284）も `file:` で参照でき、取込の対象と置き換えの対象になる。"""
    golden_set = {
        "cases": [
            {
                "id": "a",
                "query": "q",
                "relevant_document_ids": ["file:manual.pdf"],
                "required_evidence": [
                    {"id": "e1", "document_id": "file:notes.pdf", "text": "token expired"},
                    {"id": "e2", "document_id": "doc-fixed", "text": "x"},
                    {"id": "e3", "text": "y"},
                ],
            }
        ]
    }
    assert referenced_files(golden_set) == ["manual.pdf", "notes.pdf"]
    resolved = resolve_golden_set(golden_set, {"manual.pdf": "d1", "notes.pdf": "d2"}, "kb-1")
    evidence = resolved["cases"][0]["required_evidence"]
    assert [item.get("document_id") for item in evidence] == ["d2", "doc-fixed", None]
    with pytest.raises(CorpusError):
        resolve_golden_set(golden_set, {"manual.pdf": "d1"}, "kb-1")
