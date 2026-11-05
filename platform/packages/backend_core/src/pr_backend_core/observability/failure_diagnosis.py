"""例外本文を含まない、実行履歴の失敗原因の契約。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pr_backend_core.bounded_calls import (
    ErrorKind,
    NetworkFailureCause,
    classify_error,
    network_failure_cause,
)


class FailureDiagnosis(BaseModel):
    """未記録はオブジェクト無し、分類済みだが不明は kind=unknown。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: ErrorKind
    network_failure_cause: NetworkFailureCause | None = None
    # 呼出側が管理する工程名だけ。例外の本文を工程として渡さない。
    stage: str | None = Field(default=None, max_length=200)


def diagnose_failure(error: BaseException, *, stage: str | None = None) -> FailureDiagnosis:
    """既存の分類だけを保存する。ClassifiedError.detail の本文は転送しない。"""
    kind = classify_error(error).kind
    cause = network_failure_cause(error) if kind in {ErrorKind.NETWORK, ErrorKind.UNKNOWN} else None
    if cause is not None:
        kind = ErrorKind.NETWORK
    return FailureDiagnosis(kind=kind, network_failure_cause=cause, stage=stage)


def read_failure_diagnosis(value: object) -> FailureDiagnosis | None:
    """保存済みの有限分類だけを読む。旧記録の型や本文から原因を補わない。"""
    if value is None:
        return None
    try:
        return FailureDiagnosis.model_validate(value)
    except ValidationError:
        # 記録の破損と未記録を混同せず、出せない内容は未特定へ落とす。
        return FailureDiagnosis(kind=ErrorKind.UNKNOWN)
