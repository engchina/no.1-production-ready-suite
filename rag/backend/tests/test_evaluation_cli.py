"""golden set 評価 gate CLI のテスト。"""

import json
from pathlib import Path
from typing import Any

from pytest import CaptureFixture, MonkeyPatch

from app.rag import evaluation_cli


def test_evaluation_gate_cli_passes_and_writes_output_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """評価 API が passed を返す場合は exit 0 にし、レスポンスを artifact 化する。"""
    golden_set = _write_golden_set(tmp_path)
    output = tmp_path / "reports" / "evaluation-result.json"
    observed: dict[str, Any] = {}

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        observed.update(
            {
                "api_url": api_url,
                "payload": payload,
                "timeout": timeout,
                "headers": headers,
            }
        )
        return {"data": _metrics_payload(passed=True)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main(
        [
            str(golden_set),
            "--api-url",
            "http://rag.example.test/api/evaluation/run",
            "--timeout",
            "12.5",
            "--output",
            str(output),
            "--tenant-id",
            "tenant-secret",
            "--user-id",
            "user-secret",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "評価 gate passed" in captured.err
    assert "groundedness_pass_rate=1.0" in captured.err
    assert "tenant-secret" not in captured.err
    assert "user-secret" not in captured.err
    assert observed["api_url"] == "http://rag.example.test/api/evaluation/run"
    assert observed["timeout"] == 12.5
    assert observed["headers"] == {
        "X-Tenant-ID": "tenant-secret",
        "X-User-ID": "user-secret",
    }
    assert observed["payload"]["filters"] == {"status": "INDEXED"}
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["data"]["passed"] is True


def test_evaluation_gate_cli_returns_one_when_gate_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """passed=false / error_count / threshold failure は CI 用 exit 1 にする。"""
    golden_set = _write_golden_set(tmp_path)

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return {"data": _metrics_payload(passed=False, error_count=1)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main([str(golden_set), "--api-url", "http://api.test/run"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "評価 gate failed" in captured.err
    response = json.loads(captured.out)
    assert response["data"]["passed"] is False
    assert response["data"]["error_count"] == 1
    assert response["data"]["threshold_failures"]


def test_evaluation_gate_cli_detects_compare_request_and_gates_best_experiment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """experiments を含む入力は compare API に送り、rank 1 の metrics で gate する。"""
    monkeypatch.delenv("RAG_EVALUATION_API_URL", raising=False)
    compare_set = _write_compare_set(tmp_path)
    observed: dict[str, Any] = {}

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        observed.update({"api_url": api_url, "payload": payload})
        return {"data": _compare_payload(best_passed=True)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main([str(compare_set)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert observed["api_url"] == evaluation_cli.DEFAULT_EVALUATION_COMPARE_API_URL
    assert observed["payload"]["experiments"][0]["id"] == "hybrid-deep"
    assert "評価 gate passed" in captured.err
    assert "best_experiment=hybrid-deep" in captured.err
    assert "ranking_metric=recall_at_k" in captured.err
    response = json.loads(captured.out)
    assert response["data"]["best_experiment_id"] == "hybrid-deep"


def test_evaluation_gate_cli_uses_base_url_for_compare_request(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """api-base-url 指定時は request 種別に応じた endpoint を自動で付ける。"""
    monkeypatch.delenv("RAG_EVALUATION_API_URL", raising=False)
    monkeypatch.delenv("RAG_EVALUATION_COMPARE_API_URL", raising=False)
    compare_set = _write_compare_set(tmp_path)
    observed: dict[str, Any] = {}

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        observed["api_url"] = api_url
        return {"data": _compare_payload(best_passed=True)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main(
        [str(compare_set), "--api-base-url", "https://staging.example.test/"]
    )

    assert exit_code == 0
    assert observed["api_url"] == "https://staging.example.test/api/evaluation/compare"


def test_evaluation_gate_cli_prefers_explicit_api_url_over_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """明示 api-url は api-base-url より優先する。"""
    golden_set = _write_golden_set(tmp_path)
    observed: dict[str, Any] = {}

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        observed["api_url"] = api_url
        return {"data": _metrics_payload(passed=True)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main(
        [
            str(golden_set),
            "--api-url",
            "https://explicit.example.test/custom/run",
            "--api-base-url",
            "https://staging.example.test",
        ]
    )

    assert exit_code == 0
    assert observed["api_url"] == "https://explicit.example.test/custom/run"


def test_evaluation_gate_cli_uses_compare_specific_env_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """compare 専用 env URL は汎用 RAG_EVALUATION_API_URL より優先する。"""
    compare_set = _write_compare_set(tmp_path)
    observed: dict[str, Any] = {}
    monkeypatch.setenv("RAG_EVALUATION_API_URL", "https://generic.example.test/api/evaluation/run")
    monkeypatch.setenv(
        "RAG_EVALUATION_COMPARE_API_URL",
        "https://compare.example.test/api/evaluation/compare",
    )

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        observed["api_url"] = api_url
        return {"data": _compare_payload(best_passed=True)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main([str(compare_set)])

    assert exit_code == 0
    assert observed["api_url"] == "https://compare.example.test/api/evaluation/compare"


def test_evaluation_gate_cli_compare_returns_one_when_best_experiment_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """compare の最良 experiment が gate 未達なら CI 用 exit 1 にする。"""
    compare_set = _write_compare_set(tmp_path)

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return {"data": _compare_payload(best_passed=False)}

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main([str(compare_set)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "評価 gate failed" in captured.err
    assert "best_experiment=hybrid-deep" in captured.err


def test_evaluation_gate_cli_rejects_invalid_golden_set_without_query_leakage(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """評価ファイルの validation error は query 本文を出さず exit 2 にする。"""
    golden_set = tmp_path / "invalid-golden-set.json"
    golden_set.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "invalid-case",
                        "query": "INV-SECRET の承認条件",
                        "relevant_document_ids": ["doc-1"],
                    }
                ],
                "thresholds": {"recall_at_k": 1.1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fail_if_called(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        raise AssertionError("invalid golden set は API へ送らない")

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fail_if_called)

    exit_code = evaluation_cli.main([str(golden_set)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "評価ファイルの形式が不正です" in captured.err
    assert "thresholds.recall_at_k" in captured.err
    assert "INV-SECRET" not in captured.err


def test_evaluation_gate_cli_returns_three_on_api_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """API 接続・HTTP エラーは gate failure と区別して exit 3 にする。"""
    golden_set = _write_golden_set(tmp_path)

    def fake_post(
        *,
        api_url: str,
        payload: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        raise evaluation_cli.EvaluationGateError(
            "評価 API に接続できませんでした: gaierror",
            exit_code=3,
        )

    monkeypatch.setattr(evaluation_cli, "_post_evaluation_request", fake_post)

    exit_code = evaluation_cli.main([str(golden_set)])

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "評価 API に接続できませんでした" in captured.err
    assert captured.out == ""


def _write_golden_set(tmp_path: Path) -> Path:
    path = tmp_path / "golden-set.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "policy-amount",
                        "query": "承認条件はいくらですか。",
                        "relevant_document_ids": ["doc-1"],
                        "expected_answer_keywords": ["120000"],
                    }
                ],
                "top_k": 5,
                "rerank_top_n": 3,
                "mode": "hybrid",
                "filters": {"status": "indexed"},
                "thresholds": {
                    "precision_at_k": 0.3,
                    "recall_at_k": 0.8,
                    "mrr": 0.7,
                    "answer_keyword_hit_rate": 0.8,
                    "groundedness_pass_rate": 0.9,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_compare_set(tmp_path: Path) -> Path:
    path = tmp_path / "evaluation-compare.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "policy-amount",
                        "query": "承認条件はいくらですか。",
                        "relevant_document_ids": ["doc-1"],
                        "expected_answer_keywords": ["120000"],
                    }
                ],
                "experiments": [
                    {
                        "id": "hybrid-deep",
                        "top_k": 10,
                        "rerank_top_n": 5,
                        "mode": "hybrid",
                        "filters": {"status": "indexed"},
                    },
                    {
                        "id": "keyword-shallow",
                        "top_k": 5,
                        "rerank_top_n": 3,
                        "mode": "keyword",
                        "filters": {"status": "indexed"},
                    },
                ],
                "ranking_metric": "recall_at_k",
                "thresholds": {"recall_at_k": 0.8},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _metrics_payload(*, passed: bool, error_count: int = 0) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    if not passed:
        failures = [
            {
                "metric": "recall_at_k",
                "actual": 0.5,
                "threshold": 0.8,
            }
        ]
    return {
        "case_count": 1,
        "error_count": error_count,
        "evaluated_k": 3,
        "precision_at_k": 0.3333,
        "recall_at_k": 1.0 if passed else 0.5,
        "mrr": 1.0,
        "answer_keyword_hit_rate": 1.0,
        "groundedness_pass_rate": 1.0 if passed else 0.0,
        "passed": passed,
        "threshold_failures": failures,
        "case_results": [],
    }


def _compare_payload(*, best_passed: bool) -> dict[str, Any]:
    return {
        "ranking_metric": "recall_at_k",
        "best_experiment_id": "hybrid-deep",
        "results": [
            {
                "rank": 1,
                "ranking_score": 1.0 if best_passed else 0.5,
                "experiment": {
                    "id": "hybrid-deep",
                    "top_k": 10,
                    "rerank_top_n": 5,
                    "mode": "hybrid",
                    "filters": {"status": "INDEXED"},
                },
                "metrics": _metrics_payload(passed=best_passed),
            },
            {
                "rank": 2,
                "ranking_score": 0.4,
                "experiment": {
                    "id": "keyword-shallow",
                    "top_k": 5,
                    "rerank_top_n": 3,
                    "mode": "keyword",
                    "filters": {"status": "INDEXED"},
                },
                "metrics": _metrics_payload(passed=False),
            },
        ],
    }
