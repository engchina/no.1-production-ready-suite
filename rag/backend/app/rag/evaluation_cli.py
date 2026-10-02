"""golden set 評価を CI / nightly gate として実行する CLI。"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pr_backend_core.internal_http import http_client_options
from pydantic import ValidationError

from app.clients.http_retry import HttpRetryConfig, request_with_retry
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationMetrics,
    EvaluationRunRequest,
)

# 評価は job の API（投入 → 状態のポーリング → 結果。#390）で実行する。
DEFAULT_EVALUATION_API_URL = "http://localhost:8000/api/evaluation/jobs/run"
DEFAULT_EVALUATION_COMPARE_API_URL = "http://localhost:8000/api/evaluation/jobs/compare"
DEFAULT_EVALUATION_API_BASE_URL = "http://localhost:8000"
# job の終わりを待つ時間の上限（秒）。backend の job の上限（`RAG_EVALUATION_JOB_TIMEOUT_SECONDS`、
# 既定 3600 秒）で打ち切った結果を受け取れるよう、それより長くする。超えたら job を取り消す。
DEFAULT_TIMEOUT_SECONDS = 3900.0
# 状態を取得する間隔（秒）と、1 回の HTTP の呼び出しの timeout（秒）。
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0
_JOB_RUNNING_STATUS = "RUNNING"
_JOB_SUCCEEDED_STATUS = "SUCCEEDED"
EvaluationRequestKind = Literal["run", "compare"]
logger = logging.getLogger(__name__)


class EvaluationGateError(RuntimeError):
    """評価 gate CLI が利用者へ返す安全なエラー。"""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class LoadedEvaluationRequest:
    """CLI 入力ファイルを API payload と種別に分けた結果。"""

    kind: EvaluationRequestKind
    payload: dict[str, Any]


@dataclass(frozen=True)
class GateEvaluation:
    """CI gate 判定に使う metrics と compare 追加情報。"""

    metrics: EvaluationMetrics
    best_experiment_id: str | None = None
    ranking_metric: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        request = _load_evaluation_request(args.golden_set)
        response_payload = _post_evaluation_request(
            api_url=_resolve_api_url(args.api_url, args.api_base_url, request.kind),
            payload=request.payload,
            timeout=args.timeout,
            headers=_request_headers(args.tenant_id, args.user_id),
            poll_interval=args.poll_interval,
        )
        gate = _extract_gate_evaluation(response_payload)
        _write_json(response_payload, args.output)
        if args.trend_output is not None:
            _write_json(_trend_payload(response_payload, gate), args.trend_output)
    except EvaluationGateError as exc:
        print(f"評価 gate エラー: {exc}", file=sys.stderr)
        return exc.exit_code

    if _gate_failed(gate.metrics):
        print(_gate_summary(gate, passed=False), file=sys.stderr)
        return 1
    print(_gate_summary(gate, passed=True), file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-evaluation-gate",
        description="golden set を評価 API に投げ、CI / staging gate の終了コードを返します。",
    )
    parser.add_argument(
        "golden_set",
        type=Path,
        help="EvaluationRunRequest または EvaluationCompareRequest 形式の JSON ファイル。",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "評価 job の投入 API の URL。明示した場合は --api-base-url より優先します。"
            "未指定時は入力形式に応じて "
            f"{DEFAULT_EVALUATION_API_URL} または {DEFAULT_EVALUATION_COMPARE_API_URL}。"
            "以前の同期 API の URL（/api/evaluation/run・/compare）は job の API に読み替えます。"
        ),
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("RAG_EVALUATION_API_BASE_URL"),
        help=(
            "評価 API の base URL。入力形式に応じて /api/evaluation/jobs/run または "
            "/api/evaluation/jobs/compare を付与します。"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("RAG_EVALUATION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        help=(
            "評価 job の終わりを待つ秒数。超えたら job を取り消して終了コード 3 を返します。"
            f"既定値: {DEFAULT_TIMEOUT_SECONDS}"
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=_env_float("RAG_EVALUATION_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS),
        help=f"評価 job の状態を取得する間隔（秒）。既定値: {DEFAULT_POLL_INTERVAL_SECONDS}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="評価 API レスポンス JSON の保存先。未指定なら stdout に出力します。",
    )
    parser.add_argument(
        "--trend-output",
        type=Path,
        help=(
            "nightly trend 用の非機密サマリ JSON 保存先。"
            "query/context 原文や case_results は含めません。"
        ),
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


def _load_evaluation_request(path: Path) -> LoadedEvaluationRequest:
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
    kind: EvaluationRequestKind = "compare" if "experiments" in raw else "run"
    schema = EvaluationCompareRequest if kind == "compare" else EvaluationRunRequest
    try:
        request = schema.model_validate(raw)
    except ValidationError as exc:
        raise EvaluationGateError(
            "評価ファイルの形式が不正です: " + _safe_validation_error_summary(exc)
        ) from exc
    return LoadedEvaluationRequest(kind=kind, payload=request.model_dump(mode="json"))


def _resolve_api_url(
    api_url: str | None,
    api_base_url: str | None,
    kind: EvaluationRequestKind,
) -> str:
    """明示 URL > env > base URL > request 種別の既定 URL の順で決める。"""
    if api_url:
        return api_url
    if kind == "compare" and (compare_url := os.getenv("RAG_EVALUATION_COMPARE_API_URL")):
        return compare_url
    if kind == "run" and (run_url := os.getenv("RAG_EVALUATION_RUN_API_URL")):
        return run_url
    if env_url := os.getenv("RAG_EVALUATION_API_URL"):
        return env_url
    if api_base_url:
        return _evaluation_url_from_base(api_base_url, kind)
    if kind == "compare":
        return DEFAULT_EVALUATION_COMPARE_API_URL
    return DEFAULT_EVALUATION_API_URL


def _evaluation_url_from_base(base_url: str, kind: EvaluationRequestKind) -> str:
    """staging host の base URL から評価 job の投入 URL を作る。"""
    suffix = "/api/evaluation/jobs/compare" if kind == "compare" else "/api/evaluation/jobs/run"
    return f"{base_url.rstrip('/')}{suffix}"


def _job_submit_url(api_url: str) -> str:
    """以前の同期 API の URL（`/api/evaluation/run`・`/compare`）を job の投入 URL に読み替える。"""
    for kind in ("run", "compare"):
        legacy_suffix = f"/api/evaluation/{kind}"
        if api_url.rstrip("/").endswith(legacy_suffix):
            prefix = api_url.rstrip("/")[: -len(legacy_suffix)]
            return f"{prefix}/api/evaluation/jobs/{kind}"
    return api_url


def _job_status_url(submit_url: str, job_id: str) -> str:
    """投入 URL（`.../jobs/run`）から状態の URL（`.../jobs/{job_id}`）を作る。"""
    return f"{submit_url.rstrip('/').rsplit('/', 1)[0]}/{job_id}"


def _post_evaluation_request(
    *,
    api_url: str,
    payload: Mapping[str, Any],
    timeout: float,
    headers: Mapping[str, str],
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """評価 job を投入し、終わるまで状態を取得して、結果を `{"data": 結果, "job": 状態}` で返す。

    `timeout` 秒を超えても終わらなければ、job を取り消して exit code 3 の失敗にする。
    """
    if timeout <= 0:
        raise EvaluationGateError("timeout は 0 より大きい値にしてください。")
    if poll_interval <= 0:
        raise EvaluationGateError("poll-interval は 0 より大きい値にしてください。")
    submit_url = _job_submit_url(api_url)
    request_headers = {"Accept": "application/json", **headers}
    status_url: str | None = None
    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
            **http_client_options(submit_url),
        ) as client:
            job = _job_from_response(
                _request_json(client, "POST", submit_url, json=payload, headers=request_headers)
            )
            status_url = _job_status_url(submit_url, str(job["job_id"]))
            deadline = clock() + timeout
            last_progress: tuple[object, object] | None = None
            try:
                while job.get("status") == _JOB_RUNNING_STATUS:
                    progress = (job.get("completed_cases"), job.get("total_cases"))
                    if progress != last_progress:
                        print(
                            f"評価 job {job['job_id']}: {progress[0]} / {progress[1]} 件",
                            file=sys.stderr,
                        )
                        last_progress = progress
                    if clock() >= deadline:
                        _cancel_job_quietly(client, status_url, request_headers)
                        raise EvaluationGateError(
                            f"評価 job が {timeout:g} 秒以内に終わらなかったため、取り消しました。"
                            "--timeout を延ばすか、ケースを分けて評価してください。",
                            exit_code=3,
                        )
                    sleep(poll_interval)
                    job = _job_from_response(
                        _request_json(client, "GET", status_url, headers=request_headers)
                    )
            except KeyboardInterrupt:
                _cancel_job_quietly(client, status_url, request_headers)
                raise
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
    status = job.get("status")
    if status != _JOB_SUCCEEDED_STATUS:
        detail = job.get("error_message") or "理由は job の状態を確認してください。"
        raise EvaluationGateError(f"評価 job が {status} で終わりました: {detail}", exit_code=3)
    result = job.get("run_result") or job.get("compare_result")
    if result is None:
        raise EvaluationGateError("評価 job の結果がありません。", exit_code=3)
    summary = {
        key: value for key, value in job.items() if key not in {"run_result", "compare_result"}
    }
    return {"data": result, "job": summary}


def _request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = request_with_retry(
        client,
        method,
        url,
        retry=HttpRetryConfig(),
        logger=logger,
        log_extra={"api_url": url},
        **kwargs,
    )
    response.raise_for_status()
    return _decode_json_response(response.content)


def _job_from_response(response_payload: Mapping[str, Any]) -> dict[str, Any]:
    data = response_payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("job_id"), str):
        raise EvaluationGateError("評価 job の応答に job_id がありません。", exit_code=3)
    return data


def _cancel_job_quietly(
    client: httpx.Client, status_url: str | None, headers: Mapping[str, str]
) -> None:
    """CLI が待つのをやめた job を取り消す（失敗しても、元のエラーを優先する）。"""
    if status_url is None:
        return
    try:
        client.post(f"{status_url}/cancel", headers=dict(headers))
    except httpx.HTTPError as exc:
        logger.warning("evaluation_job_cancel_failed", extra={"error_type": type(exc).__name__})


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


def _extract_gate_evaluation(response_payload: Mapping[str, Any]) -> GateEvaluation:
    """ApiResponse から run metrics または compare best metrics を取り出す。"""
    data = response_payload.get("data", response_payload)
    if data is None:
        raise EvaluationGateError("評価 API レスポンスに data がありません。", exit_code=3)
    if not isinstance(data, dict):
        raise EvaluationGateError(
            "評価 API レスポンスの data が object ではありません。", exit_code=3
        )
    if "results" in data:
        try:
            comparison = EvaluationCompareResponse.model_validate(data)
        except ValidationError as exc:
            raise EvaluationGateError(
                "評価 API レスポンスの形式が不正です: " + _safe_validation_error_summary(exc),
                exit_code=3,
            ) from exc
        if not comparison.results:
            raise EvaluationGateError(
                "評価比較レスポンスに experiment results がありません。", exit_code=3
            )
        best = sorted(comparison.results, key=lambda result: result.rank)[0]
        return GateEvaluation(
            metrics=best.metrics,
            best_experiment_id=comparison.best_experiment_id or best.experiment.id,
            ranking_metric=comparison.ranking_metric,
        )
    try:
        metrics = EvaluationMetrics.model_validate(data)
    except ValidationError as exc:
        raise EvaluationGateError(
            "評価 API レスポンスの形式が不正です: " + _safe_validation_error_summary(exc),
            exit_code=3,
        ) from exc
    return GateEvaluation(metrics=metrics)


def _write_json(payload: Mapping[str, Any], output_path: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(serialized, end="")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")


def _trend_payload(response_payload: Mapping[str, Any], gate: GateEvaluation) -> dict[str, Any]:
    """nightly compare 用に raw case 詳細を除いた trend snapshot を作る。"""
    response_json = json.dumps(response_payload, ensure_ascii=False, sort_keys=True)
    data = response_payload.get("data", response_payload)
    trend: dict[str, Any] = {
        "kind": "compare" if isinstance(data, Mapping) and "results" in data else "run",
        "result_sha256": hashlib.sha256(response_json.encode("utf-8")).hexdigest(),
        "passed": not _gate_failed(gate.metrics),
        "best_experiment_id": gate.best_experiment_id,
        "ranking_metric": gate.ranking_metric,
        "metrics": _metrics_trend(gate.metrics),
    }
    if isinstance(data, Mapping) and "results" in data:
        try:
            comparison = EvaluationCompareResponse.model_validate(data)
        except ValidationError:
            return trend
        trend["experiments"] = [
            {
                "rank": result.rank,
                "id": result.experiment.id,
                "ranking_score": result.ranking_score,
                "passed": not _gate_failed(result.metrics),
                "metrics": _metrics_trend(result.metrics),
            }
            for result in sorted(comparison.results, key=lambda item: item.rank)
        ]
    return trend


def _metrics_trend(metrics: EvaluationMetrics) -> dict[str, Any]:
    """評価 metrics から trend に必要な aggregate だけを残す(指標は #591 の 9 つ)。"""
    return {
        "case_count": metrics.case_count,
        "error_count": metrics.error_count,
        **{metric: getattr(metrics, metric) for metric in EVALUATION_METRIC_NAMES},
        "metric_case_counts": dict(metrics.metric_case_counts),
        "threshold_failure_count": len(metrics.threshold_failures),
        "threshold_failures": [
            failure.model_dump(mode="json") for failure in metrics.threshold_failures
        ],
        "failure_reason_counts": dict(metrics.failure_reason_counts),
    }


def _gate_failed(metrics: EvaluationMetrics) -> bool:
    return not metrics.passed or metrics.error_count > 0 or len(metrics.threshold_failures) > 0


def _gate_summary(gate: GateEvaluation, *, passed: bool) -> str:
    metrics = gate.metrics
    status = "passed" if passed else "failed"
    prefix = f"評価 gate {status}"
    if gate.best_experiment_id is not None:
        prefix += f": best_experiment={gate.best_experiment_id}"
        if gate.ranking_metric is not None:
            prefix += f", ranking_metric={gate.ranking_metric}"
    values = ", ".join(f"{metric}={getattr(metrics, metric)}" for metric in EVALUATION_METRIC_NAMES)
    return (
        f"{prefix}: "
        f"cases={metrics.case_count}, errors={metrics.error_count}, {values}, "
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
