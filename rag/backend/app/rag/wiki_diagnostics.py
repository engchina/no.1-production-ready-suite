"""Wiki 構築の失敗から、原文や生成内容を含まない診断項目を取り出す。"""

from json import JSONDecodeError
from typing import Any

from pr_backend_core.bounded_calls import (
    BoundedCallError,
    ErrorKind,
    classify_error,
    network_failure_cause,
)
from pr_backend_core.generation_input import GenerationInputLimitError
from pr_backend_core.logging import safe_exception_fields
from pydantic import ValidationError
from rag_engine.resources.structured_failures import structured_failure_details

from app.rag.wiki_models import (
    WikiContext,
    WikiContexts,
    WikiDependency,
    WikiPage,
    WikiReference,
    WikiUnit,
)
from app.rag.wiki_packet import WikiUnitValidationError

# 実 ID を制約に持つ動的な型も、項目名は同じ基底型のスキーマで検証できる。
_MODELS = {
    model.__name__: model
    for model in (
        WikiUnit,
        WikiPage,
        WikiDependency,
        WikiContext,
        WikiContexts,
        WikiReference,
    )
}


def failure_fields(
    error: Exception, document_id: str, phase: str, fingerprint: str | None
) -> dict[str, Any]:
    classified = error if isinstance(error, BoundedCallError) else classify_error(error)
    fields = {
        "document_id": document_id,
        "wiki_phase": phase,
        "source_fingerprint": fingerprint,
        "error_type": type(error).__name__,
        "error_kind": classified.kind.value,
        "http_status": classified.status,
        **safe_exception_fields(error),
    }
    if isinstance(error, BoundedCallError):
        fields["attempts"] = error.attempts
    if classified.kind == ErrorKind.NETWORK and (network_cause := network_failure_cause(error)):
        fields["network_cause"] = network_cause
    schema = None
    cause: BaseException | None = error
    for _ in range(4):
        if cause is None:
            break
        if isinstance(cause, WikiUnitValidationError):
            fields["unit_id_hash"] = cause.unit_id_hash
        if isinstance(cause, JSONDecodeError):
            fields.update(json_line=cause.lineno, json_column=cause.colno)
        if isinstance(cause, GenerationInputLimitError) and "estimated_input_tokens" not in fields:
            fields.update(
                estimated_input_tokens=cause.estimated_tokens, input_limit_tokens=cause.limit_tokens
            )
        if isinstance(cause, ValidationError):
            schema = _MODELS.get(cause.title.removeprefix("Scoped"))
            break
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    if schema is not None:
        fields["validation_schema"] = schema.__name__
    # スキーマの項目名・配列位置・規則と、既知のプロバイダー状態だけを記録する。
    details = structured_failure_details(error, schema)
    for key in ("validation_errors", "provider_status", "incomplete_reason"):
        if key in details:
            fields[key] = details[key]
    return fields
