"""回答の最終の検証（#1246）。

`agent_final_validation_enabled` のとき、組み込み Runtime は回答を保存する前に、その Run で RAG が
返した根拠（`rag_search` / `rag_retrieve_evidence` の `document_id`・`chunk_id`）で、RAG の MCP
`rag_validate_answer` を呼ぶ。呼ぶのはモデルではなく Control Plane で、ツールの境界
（`tool_registry.invoke` のポリシー・監査、Run の利用者のサービストークン、Run の step）を通す。

- 結果は Run の成果物（kind=`answer_validation`・「回答の検証」）に残す。
- valid でなければ回答を作り直さず、末尾に「確かめられていない点」を足す。
- 検証そのものが失敗したら「この回答は検証できませんでした。」を足す（回答は消さない）。
- RAG の根拠を使っていない Run は検証しない（`skipped` / `no_rag_evidence`）。

ここは根拠の参照の集め方・成果物の内容・回答に足す文だけを持つ（呼び出しは `builtin_runtime`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.features.agent.support_task import RAG_RETRIEVE_EVIDENCE, RAG_SEARCH
from app.features.agent.tools import MCP_TOOL_SEPARATOR, mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

ANSWER_VALIDATION_KIND = "answer_validation"
ANSWER_VALIDATION_NAME = "回答の検証"
VALIDATE_ANSWER_TOOL = "rag_validate_answer"
# 検証に渡す根拠の参照の上限（`rag_validate_answer` の契約の maxItems）。
MAX_VALIDATION_EVIDENCE = 30
# 契約の上限（質問は切り詰めて渡す。回答は切り詰めると別の文の検証になるため、超えたら検証しない）。
MAX_QUERY_CHARS = 8000
MAX_ANSWER_CHARS = 20000

STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_NO_RAG_EVIDENCE = "no_rag_evidence"
REASON_VALIDATOR_UNAVAILABLE = "validator_unavailable"
REASON_EMPTY_ANSWER = "empty_answer"
REASON_ANSWER_TOO_LONG = "answer_too_long"
REASON_CONNECTION_NOT_FOUND = "connection_not_found"

UNVERIFIED_NOTICE = "この回答は検証できませんでした。"
UNVERIFIED_HEADING = "確かめられていない点"
_EVIDENCE_TOOLS = frozenset({RAG_SEARCH, RAG_RETRIEVE_EVIDENCE})
_CLAIM_LABELS = {"contradicted": "根拠と矛盾", "unsupported": "根拠で確かめられない"}
_MAX_LISTED_CLAIMS = 5
_MAX_QUOTE_CHARS = 80
_MAX_REASON_CHARS = 80


def _short(value: object, limit: int) -> str:
    """1 行にして `limit` 文字まで（超えたら末尾を「…」にする）。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _connection_prefix(function_name: str) -> str | None:
    """`<接続>__<ツール>` の接続の部分（MCP 接続のツールでなければ None）。"""
    if MCP_TOOL_SEPARATOR not in function_name:
        return None
    return function_name[: -len(mcp_base_tool_name(function_name)) - len(MCP_TOOL_SEPARATOR)]


def run_evidence_refs(steps: list[RunStep]) -> tuple[str | None, list[JsonObject]]:
    """その Run で RAG が返した根拠の参照（新しい呼び出しから順、重複なし、最大 30 件）。

    根拠の id は返した RAG のものなので、最後に根拠を返した MCP 接続の根拠だけを渡す。返すのは、
    その接続の根拠のツールの function tool の名前（接続を決める）と、参照の一覧。
    """
    tool_name: str | None = None
    prefix: str | None = None
    refs: list[JsonObject] = []
    seen: set[tuple[str, str]] = set()
    for step in reversed(steps):
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if mcp_base_tool_name(call.name) not in _EVIDENCE_TOOLS:
            continue
        evidence = (result.output or {}).get("evidence")
        if not isinstance(evidence, list):
            continue
        step_prefix = _connection_prefix(call.name)
        if step_prefix is None:
            continue
        if prefix is None:
            prefix, tool_name = step_prefix, call.name
        elif step_prefix != prefix:
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            document_id, chunk_id = item.get("document_id"), item.get("chunk_id")
            if not isinstance(document_id, str) or not isinstance(chunk_id, str):
                continue
            if not document_id or not chunk_id or (document_id, chunk_id) in seen:
                continue
            seen.add((document_id, chunk_id))
            refs.append({"document_id": document_id, "chunk_id": chunk_id})
    return tool_name, refs[:MAX_VALIDATION_EVIDENCE]


def validation_content(
    status: str,
    *,
    reason: str | None = None,
    message: str | None = None,
    connection: str | None = None,
    tool_name: str | None = None,
    step_id: str | None = None,
    evidence: list[JsonObject] | None = None,
    result: JsonObject | None = None,
) -> JsonObject:
    """成果物「回答の検証」の内容。"""
    valid = result.get("valid") if isinstance(result, dict) else None
    return {
        "status": status,
        "reason": reason,
        "message": message,
        "valid": valid if isinstance(valid, bool) else None,
        "connection": connection,
        "tool_name": tool_name,
        "step_id": step_id,
        "evidence": list(evidence or []),
        "result": result,
    }


def _refs_count(value: object) -> int:
    return len([item for item in value if isinstance(item, dict)]) if isinstance(value, list) else 0


def _claim_line(claim: JsonObject) -> str:
    """「- 根拠で確かめられない: 「段落」（理由）」の 1 行（段落・理由は 80 文字まで）。"""
    quote = _short(claim.get("answer_quote"), _MAX_QUOTE_CHARS)
    reason = _short(claim.get("reason"), _MAX_REASON_CHARS)
    suffix = f"（{reason}）" if reason else ""
    return f"- {_CLAIM_LABELS[str(claim.get('status'))]}: 「{quote}」{suffix}"


def unverified_points(result: JsonObject) -> list[str] | None:
    """valid でない検証の結果から、回答の末尾に足す「確かめられていない点」の行。

    足さなくてよい（valid・確かめる主張が無い）なら None、検証として使えない結果なら空の list。
    """
    if result.get("valid") is True:
        return None
    status = result.get("status")
    if status == "no_claims":
        return None
    if status not in {"completed", "no_evidence"}:
        return []
    claims = [
        item
        for item in result.get("claims") or []
        if isinstance(item, dict) and item.get("status") in _CLAIM_LABELS
    ]
    lines = [_claim_line(item) for item in claims[:_MAX_LISTED_CLAIMS]]
    if len(claims) > _MAX_LISTED_CLAIMS:
        lines.append(f"- ほか {len(claims) - _MAX_LISTED_CLAIMS} 件の主張")
    stale = _refs_count(result.get("stale_evidence"))
    if stale:
        lines.append(f"- 根拠の {stale} 件は文書の古い版です。最新の版で確かめ直してください。")
    missing = _refs_count(result.get("missing_evidence"))
    if missing:
        lines.append(f"- 根拠の {missing} 件は見つかりません（削除された・参照できない）。")
    if not lines:
        lines.append("- 根拠で裏付けられた主張がありません。")
    return lines


def annotate_answer(answer: str, lines: list[str] | None) -> str:
    """回答の末尾に「確かめられていない点」を足す（行が無ければ「検証できませんでした」）。"""
    if lines is None:
        return answer
    if not lines:
        return with_unverified_notice(answer)
    return f"{answer.rstrip()}\n\n**{UNVERIFIED_HEADING}**\n\n" + "\n".join(lines)


def with_unverified_notice(answer: str) -> str:
    return f"{answer.rstrip()}\n\n{UNVERIFIED_NOTICE}"
