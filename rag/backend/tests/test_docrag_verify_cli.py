"""DocRAG の検証 CLI(回答評価・回帰・CRAG goldset)。API は MockTransport でスタブする。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.docrag_verify_cli import judge, main, split_sections

ANSWER = (
    "市区町村長名は基本情報登録で変更します。\n"
    "操作手順（基本情報登録）\n"
    "1. 市区町村長名を入力し、実行を押します。\n"
    "根拠：manual.pdf\n"
    "資料からは確認できない点\n"
    "印影の差し替え手順\n"
)


def _envelope(data: Any, status: int = 200, errors: list[str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={"data": data, "error_messages": errors or [], "warning_messages": []}
    )


class FakeApi:
    def __init__(self, *, strategy: str = "docrag", fail_ids: set[str] | None = None) -> None:
        self.strategy = strategy
        self.fail_ids = fail_ids or set()
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body, dict(request.headers)))
        if request.url.path == "/api/search":
            if body["query"] in self.fail_ids:
                return _envelope(None, 503, ["検索が一時的に利用できません。"])
            return _envelope(
                {
                    "trace_id": f"trace-{len(self.requests)}",
                    "answer": ANSWER,
                    "citations": [],
                    "diagnostics": {
                        "retrieval_strategy": self.strategy,
                        "docrag": {"confidence": "high", "needs_human_review": False},
                    },
                }
            )
        if request.url.path.endswith("/evaluation"):
            score = 18 if "承認" in body["standard_answer"] else 10
            return _envelope(
                {
                    "trace_id": request.url.path.split("/")[-2],
                    "evaluation": {
                        "status": "completed",
                        "total_score": score,
                        "passed": score >= 16,
                    },
                }
            )
        return _envelope(None, 404, ["not found"])


def _write(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_split_sections_and_judge_follow_rag_poc_rules() -> None:
    sections = split_sections(ANSWER)

    assert sections["summary"] == "市区町村長名は基本情報登録で変更します。"
    assert sections["操作手順"].startswith("基本情報登録")
    assert "manual.pdf" not in "".join(sections.values())
    result = judge(
        ANSWER,
        {
            "applied_all": [["基本情報登録"], ["市区町村長名", "実行"]],
            "applied_excludes": ["印影"],
            "gap_contains": ["印影"],
        },
    )
    assert result["passed"] is True
    # gap の節は「適用」に数えない。
    assert judge(ANSWER, {"applied_all": [["印影"]]})["passed"] is False


def test_answers_evaluates_resumes_and_summarizes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    qa = _write(
        tmp_path / "qa.json",
        {
            "items": [
                {"id": "a1", "question": "承認者は？", "standard_answer": "部長の承認が必要"},
                {"id": "a2", "question": "締め日は？", "standard_answer": "月末"},
                {"id": "a3", "question": "失敗する質問", "standard_answer": "x"},
            ]
        },
    )
    out = tmp_path / "out"
    api = FakeApi(fail_ids={"失敗する質問"})
    args = ["answers", "--qa", str(qa), "--business-view", "bv-1", "--out", str(out)]

    assert main([*args, "--tenant-id", "tenant-a"], transport=httpx.MockTransport(api)) == 0
    first_calls = len(api.requests)
    # 2 回目は保存済みの件を飛ばす(失敗した件も記録済みなので再実行しない)。
    assert main(args, transport=httpx.MockTransport(api)) == 0

    assert len(api.requests) == first_calls
    search_path, search_body, headers = api.requests[0]
    assert (search_path, search_body) == (
        "/api/search",
        {"query": "承認者は？", "business_view_ids": ["bv-1"]},
    )
    assert headers["x-tenant-id"] == "tenant-a"
    a1 = json.loads((out / "a1.json").read_text(encoding="utf-8"))
    assert a1["evaluation"]["total_score"] == 18
    assert "検索が一時的に利用できません" in json.loads((out / "a3.json").read_text())["error"]
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("評価完了 2 / 3 件、合格率 50%、平均点 14.00")
    assert "| a3 | エラー | — | — |" in summary
    assert "合格率 50%" in capsys.readouterr().out


def test_answers_records_error_when_view_is_not_docrag(tmp_path: Path) -> None:
    qa = _write(tmp_path / "qa.json", [{"id": "a1", "question": "q", "standard_answer": "s"}])
    api = FakeApi(strategy="hybrid_rrf")

    main(
        ["answers", "--qa", str(qa), "--business-view", "bv-1", "--out", str(tmp_path / "out")],
        transport=httpx.MockTransport(api),
    )

    record = json.loads((tmp_path / "out" / "a1.json").read_text(encoding="utf-8"))
    assert "DocRAG ではない" in record["error"]
    assert [path for path, _, _ in api.requests] == ["/api/search"]


def test_regression_runs_repeats_resumes_and_summarizes(tmp_path: Path) -> None:
    cases = _write(
        tmp_path / "cases.json",
        {
            "cases": [
                {
                    "id": "q01",
                    "question": "市長名を変更したい",
                    "run_id": "rag_poc の run(読み捨てる)",
                    "expect": {"applied_all": [["基本情報登録"]], "gap_contains": ["印影"]},
                },
                {
                    "id": "q02",
                    "question": "別の質問",
                    "expect": {"applied_all": [["存在しない語"]]},
                },
            ]
        },
    )
    out = tmp_path / "out"
    api = FakeApi()
    args = [
        "regression",
        "--cases",
        str(cases),
        "--business-view",
        "bv-1",
        "--out",
        str(out),
        "--repeat",
        "2",
    ]

    main([*args, "--only", "q01"], transport=httpx.MockTransport(api))
    assert sorted(path.name for path in out.glob("q*.json")) == ["q01-1.json", "q01-2.json"]
    main(args, transport=httpx.MockTransport(api))

    assert len(api.requests) == 4  # q01 × 2 回 + 再開後の q02 × 2 回
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "| q01 | pass (high) | pass (high) | 2/2 |" in summary
    assert "| q02 | FAIL (high) | FAIL (high) | 0/2 | applied_all:存在しない語 |" in summary

    main([*args, "--summary-only"], transport=httpx.MockTransport(api))
    assert len(api.requests) == 4


def test_crag_goldset_returns_rag_poc_exit_code(tmp_path: Path) -> None:
    def case(case_id: str, crag_answer: str) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "question": "質問",
            "expected_evidence_ids": ["e1"],
            "expected_answer_terms": ["赤枠", "実行"],
            "variants": {
                "standard_rag": {"retrieved_ids": ["e1"], "answer": "わかりません。"},
                "crag": {"retrieved_ids": ["e1"], "answer": crag_answer},
                "crag_rerank": {"retrieved_ids": ["e1"], "answer": crag_answer},
            },
        }

    better = _write(tmp_path / "better.json", {"cases": [case("c1", "赤枠の実行を押します。")]})
    worse = _write(tmp_path / "worse.json", {"cases": [case("c1", "青枠を押します。")]})

    assert main(["crag-goldset", str(better)]) == 0
    assert main(["crag-goldset", str(worse)]) == 1
