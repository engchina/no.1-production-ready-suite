"""golden set 評価を CI / nightly gate として実行する CLI。"""

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.schemas.evaluation import EvaluationMetrics, EvaluationRunRequest

DEFAULT_EVALUATION_API_URL = "http://localhost:8000/api/evaluation/run"
DEFAULT_TIMEOUT_SECONDS = 300.0


class EvaluationGateError(RuntimeError):
    """評価 gate CLI が利用者へ返す安全なエラー。"""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        request_payload = _load_evaluation_request(args.golden_set)
        response_payload = _post_evaluation_request(
            api_url=args.api_url,
            payload=request_payload,
            timeout=args.timeout,
            headers=_request_headers(args.tenant_id, args.user_id),
        )
        metrics = _extract_metrics(response_payload)
        _write_json(response_payload, args.output)
    except EvaluationGateError as exc:
        print(f"評価 gate エラー: {exc}", file=sys.stderr)
        return exc.exit_code

    if _gate_failed(metrics):
        print(_gate_summary(metrics, passed=False), file=sys.stderr)
        return 1
    print(_gate_summary(metrics, passed=True), file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-evaluation-gate",
        description="golden set を評価 API に投げ、CI / staging gate の終了コードを返します。",
    )
    parser.add_argument(
        "golden_set",
        type=Path,
        help="EvaluationRunRequest 形式の golden set JSON ファイル。",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("RAG_EVALUATION_API_URL", DEFAULT_EVALUATION_API_URL),
        help=f"評価 API URL。既定値: {DEFAULT_EVALUATION_API_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("RAG_EVALUATION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        help=f"評価 API 呼び出し timeout 秒。既定値: {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="評価 API レスポンス JSON の保存先。未指定なら stdout に出力します。",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("RAG_EVALUATION_TENANT_ID"),
        help="評価対象 tenant の X-Tenant-ID。値は CLI 出力へ表示しません。",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("RAG_EVALUATION_USER_ID"),
        help="評価実行者の X-User-ID。値は CLI 出力へ表示しません。",
    )
    return parser


def _load_evaluation_request(path: Path) -> dict[str, Any]:
    """golden set JSON を読み、API request schema として検証する。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationGateError(f"評価ファイルが見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationGateError(
            f"評価ファイルが JSON として読めません: line={exc.lineno}, column={exc.colno}"
        ) from exc
    if not isinstance(raw, dict):
        raise EvaluationGateError("評価ファイルの root は JSON object にしてください。")
    try:
        request = EvaluationRunRequest.model_validate(raw)
    except ValidationError as exc:
        raise EvaluationGateError(
            "評価ファイルの形式が不正です: " + _safe_validation_error_summary(exc)
        ) from exc
    return request.model_dump(mode="json")


def _post_evaluation_request(
    *,
    api_url: str,
    payload: Mapping[str, Any],
    timeout: float,
    headers: Mapping[str, str],
) -> dict[str, Any]:
    """評価 API へ JSON request を送る。"""
    if timeout <= 0:
        raise EvaluationGateError("timeout は 0 より大きい値にしてください。")
    request_headers = {"Accept": "application/json", **headers}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(api_url, json=payload, headers=request_headers)
            response.raise_for_status()
            return _decode_json_response(response.content)
    except httpx.InvalidURL as exc:
        raise EvaluationGateError("評価 API URL が不正です。") from exc
    except httpx.HTTPStatusError as exc:
        message = (
            f"評価 API が HTTP {exc.response.status_code} を返しました。"
            "request_id とサーバログを確認してください。"
        )
        raise EvaluationGateError(
            message,
            exit_code=3,
        ) from exc
    except httpx.TimeoutException as exc:
        raise EvaluationGateError("評価 API 呼び出しが timeout しました。", exit_code=3) from exc
    except httpx.RequestError as exc:
        raise EvaluationGateError(
            f"評価 API に接続できませんでした: {type(exc).__name__}",
            exit_code=3,
        ) from exc


def _decode_json_response(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationGateError(
            "評価 API レスポンスが JSON として読めませんでした。",
            exit_code=3,
        ) from exc
    if not isinstance(decoded, dict):
        raise EvaluationGateError(
            "評価 API レスポンスの root が JSON object ではありません。",
            exit_code=3,
        )
    return decoded


def _extract_metrics(response_payload: Mapping[str, Any]) -> EvaluationMetrics:
    """ApiResponse または metrics object から評価 metrics を取り出す。"""
    data = response_payload.get("data", response_payload)
    if data is None:
        raise EvaluationGateError("評価 API レスポンスに data がありません。", exit_code=3)
    if not isinstance(data, dict):
        raise EvaluationGateError(
            "評価 API レスポンスの data が object ではありません。", exit_code=3
        )
    try:
        return EvaluationMetrics.model_validate(data)
    except ValidationError as exc:
        raise EvaluationGateError(
            "評価 API レスポンスの形式が不正です: " + _safe_validation_error_summary(exc),
            exit_code=3,
        ) from exc


def _write_json(payload: Mapping[str, Any], output_path: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(serialized, end="")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")


def _gate_failed(metrics: EvaluationMetrics) -> bool:
    return not metrics.passed or metrics.error_count > 0 or len(metrics.threshold_failures) > 0


def _gate_summary(metrics: EvaluationMetrics, *, passed: bool) -> str:
    status = "passed" if passed else "failed"
    return (
        f"評価 gate {status}: cases={metrics.case_count}, errors={metrics.error_count}, "
        f"precision_at_k={metrics.precision_at_k}, recall_at_k={metrics.recall_at_k}, "
        f"mrr={metrics.mrr}, answer_keyword_hit_rate={metrics.answer_keyword_hit_rate}, "
        f"threshold_failures={len(metrics.threshold_failures)}"
    )


def _request_headers(tenant_id: str | None, user_id: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    if user_id:
        headers["X-User-ID"] = user_id
    return headers


def _safe_validation_error_summary(error: ValidationError) -> str:
    """入力値を出さず、path と error type だけを利用者へ返す。"""
    summaries: list[str] = []
    for item in error.errors():
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        error_type = item.get("type", "validation_error")
        summaries.append(f"{loc}:{error_type}")
    return ", ".join(summaries)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
