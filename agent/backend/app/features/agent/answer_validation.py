"""回答の最終の検証（#1246・#1277）。

組み込み Runtime は回答を保存する前に（`agent_final_validation_enabled`。既定 on）、その Run で
RAG が返した根拠（`rag_search` / `rag_retrieve_evidence` の `document_id`・`chunk_id`）で、
RAG の MCP `rag_validate_answer` を呼ぶ。呼ぶのはモデルではなく Control Plane で、ツールの境界
（`tool_registry.invoke` のポリシー・監査、Run の利用者のサービストークン、Run の step）を通す。

- 根拠の id は返した RAG のものなので、根拠を返した MCP 接続ごとに、その接続の根拠で 1 回ずつ
  呼び、段落（`answer_quote`）ごとに判定をまとめる。
- 結果は Run の成果物（kind=`answer_validation`・「回答の検証」）に残す（接続ごとの結果は
  `connections`）。状態は `completed`（判定が出た）/ `unvalidated`（確かめられなかった）/
  `skipped`（確かめる対象が無い）。
- 接続ごとに、その接続の最も新しい `rag_search` の `requests`・`gaps`・`guide`（無ければ
  `rag_lookup_guides` の最上位の業務ガイド）も渡し、RAG の決定的な検査（要求の充足・手順の順序と
  分岐・影響範囲・承認。#1276）を動かす。
- 判定が valid でなければ、根拠で確かめられなかった段落（裏付けが無い・矛盾など）を回答から外し、
  外した内容と理由を「確かめられていない点」として足す（確かめていない操作の手順を公開しない。
  handoff §12）。根拠で確かめた段落と、主張ではない行（見出しなど）は残す。決定的な検査の error も
  同じく扱う: 手順・影響範囲の error は手順を確かめられないので本文を載せず、要求の error（答えて
  いない要求を示していない）は本文を残して不足を示す。warning は出さない。
- 検証そのもの（呼び出し・接続・応答）が失敗したら `unvalidated` にし、回答は消さずに
  「この回答は検証できませんでした。」を足す（基盤の障害で内容を落とさない）。
- RAG の根拠を使っていない Run は、RAG の根拠のツールを持つ Agent なら `unvalidated`
  （`no_rag_evidence`）にして「資料と照らし合わせて確かめていない」と足し、持たない Agent は
  `skipped`（回答はそのまま）。

ここは根拠の参照の集め方・判定のまとめ方・成果物の内容・回答の組み立てだけを持つ（呼び出しは
`builtin_runtime`）。
"""

from __future__ import annotations

import re
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

# 判定が出た。
STATUS_COMPLETED = "completed"
# 確かめる対象が無い（回答が空・RAG の根拠のツールを持たない Agent の根拠の無い回答）。
STATUS_SKIPPED = "skipped"
# 確かめられなかった（検証の失敗・RAG の根拠を使わなかった回答）。回答に注記を足す。
STATUS_UNVALIDATED = "unvalidated"

# 決定的な検査の入力（`requests`・`gaps`・`guide.conditions`）の件数の上限（契約の maxItems）。
MAX_CHECK_ITEMS = 30
RAG_LOOKUP_GUIDES = "rag_lookup_guides"
REQUEST_STATUSES = frozenset({"addressed", "partial", "missing", "unknown"})
# 回答に手順を書く業務ガイドの判断（手順・影響範囲を確かめる）。
GUIDE_DECISIONS = frozenset({"answer", "branch"})
# 本文を載せない決定的な検査の error（手順・影響範囲・業務ガイドの版。
# 確かめていない手順を出さない）。
WITHHOLDING_CHECKS = frozenset({"guide", "guide_steps", "impact"})

REASON_NO_RAG_EVIDENCE = "no_rag_evidence"
REASON_VALIDATOR_UNAVAILABLE = "validator_unavailable"
REASON_EMPTY_ANSWER = "empty_answer"
REASON_ANSWER_TOO_LONG = "answer_too_long"
REASON_CONNECTION_NOT_FOUND = "connection_not_found"
REASON_UNUSABLE_RESULT = "unusable_result"
REASON_VALIDATION_ERROR = "validation_error"

UNVERIFIED_NOTICE = "この回答は検証できませんでした。"
NO_EVIDENCE_NOTICE = "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。"
UNVERIFIED_HEADING = "確かめられていない点"
WITHHELD_INTRO = "根拠で確かめられなかった次の内容は、回答に載せていません。"
NOTHING_CONFIRMED = "根拠で確かめられた内容はありませんでした。"
GUIDE_WITHHELD = (
    "業務ガイドの手順・影響範囲と照らして確かめられない点があるため、回答の本文は載せていません。"
)
EVIDENCE_TOOLS = frozenset({RAG_SEARCH, RAG_RETRIEVE_EVIDENCE})
# 回答に載せない段落の判定（RAG の `is_valid` が検証に通さない判定と同じ）。
BLOCKING_CLAIM_STATUSES = frozenset({"unsupported", "contradicted", "citation_error", "unassessed"})
_CLAIM_LABELS = {
    "contradicted": "根拠と矛盾",
    "unsupported": "根拠で確かめられない",
    "citation_error": "出典を確かめられない",
    "unassessed": "確かめが終わっていない",
}
# 判定として使える `rag_validate_answer` の結果の status（それ以外は検証の失敗として扱う）。
_USABLE_RESULT_STATUSES = ("completed", "no_evidence", "no_claims")
_MAX_LISTED_CLAIMS = 5
_MAX_QUOTE_CHARS = 80
_MAX_REASON_CHARS = 80
_MAX_LISTED_FINDINGS = 5
_MAX_FINDING_CHARS = 120


def _short(value: object, limit: int) -> str:
    """1 行にして `limit` 文字まで（超えたら末尾を「…」にする）。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _connection_prefix(function_name: str) -> str | None:
    """`<接続>__<ツール>` の接続の部分（MCP 接続のツールでなければ None）。"""
    if MCP_TOOL_SEPARATOR not in function_name:
        return None
    return function_name[: -len(mcp_base_tool_name(function_name)) - len(MCP_TOOL_SEPARATOR)]


def run_evidence_groups(steps: list[RunStep]) -> list[tuple[str, list[JsonObject]]]:
    """その Run で RAG が返した根拠の参照を、MCP 接続ごとにまとめる（#1277）。

    根拠の id は返した RAG のものなので、接続ごとに分けて検証する。返すのは、接続ごとの
    （その接続の最も新しい根拠のツールの function tool の名前〔接続を決める〕, 参照の一覧）で、
    最も新しく根拠を返した接続から順。参照は新しい呼び出しから順、接続の中で重複なし、最大 30 件。
    """
    groups: dict[str, tuple[str, list[JsonObject], set[tuple[str, str]]]] = {}
    for step in reversed(steps):
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if mcp_base_tool_name(call.name) not in EVIDENCE_TOOLS:
            continue
        evidence = (result.output or {}).get("evidence")
        if not isinstance(evidence, list):
            continue
        prefix = _connection_prefix(call.name)
        if prefix is None:
            continue
        _tool_name, refs, seen = groups.setdefault(prefix, (call.name, [], set()))
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
    return [
        (tool_name, refs[:MAX_VALIDATION_EVIDENCE])
        for tool_name, refs, _seen in groups.values()
        if refs
    ]


def _text_value(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _completed_outputs(
    steps: list[RunStep], prefix: str, tool: str
) -> list[tuple[JsonObject, JsonObject]]:
    """接続 `prefix` の、成功した `tool` の呼び出しの（引数, 出力）（新しい順）。"""
    found: list[tuple[JsonObject, JsonObject]] = []
    for step in reversed(steps):
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if not isinstance(result.output, dict) or _connection_prefix(call.name) != prefix:
            continue
        if mcp_base_tool_name(call.name) == tool:
            found.append((call.arguments, result.output))
    return found


def _guide_input(guide: object, profile_id: object, conditions: object) -> JsonObject | None:
    """`rag_validate_answer` の `guide`（手順を示す判断の業務ガイドで、プロファイルが分かるとき）。

    確かめる質問・担当への引き継ぎの判断（`clarify` / `handoff`）では回答に手順が無いので渡さない
    （影響範囲・承認の検査が、手順を書かない回答を誤って止めるため）。
    """
    if not isinstance(guide, dict) or guide.get("decision", "answer") not in GUIDE_DECISIONS:
        return None
    guide_id = _text_value(guide.get("guide_id"), 64)
    revision = guide.get("revision")
    profile = _text_value(profile_id, 128)
    if guide_id is None or profile is None or not isinstance(revision, int) or revision < 1:
        return None
    known: dict[str, str] = {}
    if isinstance(conditions, dict):
        known.update(
            {
                key: value
                for key, value in conditions.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        )
    for item in guide.get("known_conditions") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            value = item.get("value")
            if isinstance(value, str) and value:
                known[item["id"]] = value
    content: JsonObject = {
        "search_answer_profile_id": profile,
        "guide_id": guide_id,
        "revision": revision,
    }
    if known:
        content["conditions"] = dict(list(known.items())[:MAX_CHECK_ITEMS])
    return content


def connection_check_inputs(steps: list[RunStep], evidence_tool: str) -> JsonObject:
    """`rag_validate_answer` の決定的な検査の入力（`requests`・`gaps`・`guide`。#1276・#1277）。

    根拠を返した接続の最も新しい `rag_search` の要求ごとの充足・不足・業務ガイドを渡す。
    `rag_search` が業務ガイドを返さなければ、同じ接続の最も新しい `rag_lookup_guides` の最上位の
    業務ガイドを使う。分からない項目は渡さない（その検査は RAG が行わない）。
    """
    prefix = _connection_prefix(evidence_tool)
    if prefix is None:
        return {}
    inputs: JsonObject = {}
    guide: JsonObject | None = None
    searches = _completed_outputs(steps, prefix, RAG_SEARCH)
    if searches:
        arguments, output = searches[0]
        requests = [
            {
                "id": request_id,
                "text": _text_value(item.get("text"), 2000) or "",
                "status": item["status"],
            }
            for item in output.get("requests") or []
            if isinstance(item, dict)
            and (request_id := _text_value(item.get("id"), 64))
            and item.get("status") in REQUEST_STATUSES
        ]
        if requests:
            inputs["requests"] = requests[:MAX_CHECK_ITEMS]
        gaps = [gap for item in output.get("gaps") or [] if (gap := _text_value(item, 2000))]
        if gaps:
            inputs["gaps"] = gaps[:MAX_CHECK_ITEMS]
        provenance = output.get("provenance")
        profile = (
            provenance.get("search_answer_profile_id") if isinstance(provenance, dict) else None
        ) or arguments.get("search_answer_profile_id")
        guide = _guide_input(output.get("guide"), profile, arguments.get("conditions"))
    if guide is None:
        lookups = _completed_outputs(steps, prefix, RAG_LOOKUP_GUIDES)
        if lookups:
            arguments, output = lookups[0]
            guides = output.get("guides")
            if isinstance(guides, list) and guides:
                guide = _guide_input(
                    guides[0],
                    arguments.get("search_answer_profile_id"),
                    arguments.get("conditions"),
                )
    if guide is not None:
        inputs["guide"] = guide
    return inputs


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
    """成果物「回答の検証」の内容（接続ごとの結果にも使う）。"""
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


def usable_result(result: object) -> bool:
    """`rag_validate_answer` の結果が判定として使えるか（使えなければ検証の失敗として扱う）。"""
    return isinstance(result, dict) and result.get("status") in _USABLE_RESULT_STATUSES


def _refs_count(value: object) -> int:
    return len([item for item in value if isinstance(item, dict)]) if isinstance(value, list) else 0


def _claim_line(claim: JsonObject) -> str:
    """「- 根拠で確かめられない: 「段落」（理由）」の 1 行（段落・理由は 80 文字まで）。"""
    quote = _short(claim.get("answer_quote"), _MAX_QUOTE_CHARS)
    reason = _short(claim.get("reason"), _MAX_REASON_CHARS)
    suffix = f"（{reason}）" if reason else ""
    return f"- {_CLAIM_LABELS[str(claim.get('status'))]}: 「{quote}」{suffix}"


def _paragraph_claim(claims: list[JsonObject]) -> JsonObject:
    """1 つの接続の、同じ段落の判定（段落に複数の主張があれば最も厳しいもの）。"""
    for claim in claims:
        if claim.get("status") == "contradicted":
            return claim
    for claim in claims:
        if claim.get("status") in BLOCKING_CLAIM_STATUSES:
            return claim
    return next((claim for claim in claims if claim.get("status") == "supported"), claims[0])


def merge_results(results: list[JsonObject]) -> JsonObject:
    """接続ごとの `rag_validate_answer` の結果（判定として使えるもの）を 1 つにまとめる（#1277）。

    どの接続も同じ回答を同じ規則で段落に分けて確かめるので、段落（`answer_quote`）ごとにまとめる。
    接続の中では段落の最も厳しい判定、接続をまたいでは、どれかの接続が矛盾と判定すれば矛盾、
    そうでなくどれかの接続が通せば（裏付けあり・確かめる必要が無い行）その判定、どの接続も
    通さなければ最初の接続の判定（ほかの接続の根拠に無いだけで外さない）。結果が 1 つならそのまま。
    """
    if len(results) == 1:
        return results[0]
    per_result: list[dict[str, JsonObject]] = []
    order: list[str] = []
    for result in results:
        grouped: dict[str, list[JsonObject]] = {}
        for claim in result.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            quote = str(claim.get("answer_quote") or "")
            if quote not in grouped and quote not in order:
                order.append(quote)
            grouped.setdefault(quote, []).append(claim)
        per_result.append({quote: _paragraph_claim(items) for quote, items in grouped.items()})
    claims: list[JsonObject] = []
    for quote in order:
        judged = [item[quote] for item in per_result if quote in item]
        chosen = (
            next((item for item in judged if item.get("status") == "contradicted"), None)
            or next(
                (item for item in judged if item.get("status") not in BLOCKING_CLAIM_STATUSES),
                None,
            )
            or judged[0]
        )
        claims.append(dict(chosen))
    counts: dict[str, int] = {}
    for claim in claims:
        key = str(claim.get("status"))
        counts[key] = counts.get(key, 0) + 1
    stale = [item for result in results for item in result.get("stale_evidence") or []]
    missing = [item for result in results for item in result.get("missing_evidence") or []]
    statuses = {result.get("status") for result in results}
    status = next(item for item in _USABLE_RESULT_STATUSES if item in statuses)
    # 決定的な検査の指摘は接続ごとに同じ回答を確かめたものなので、重複を除いてすべて残す。
    findings: list[JsonObject] = []
    seen_findings: set[tuple[str, str, str]] = set()
    for result in results:
        for item in result.get("findings") or []:
            if not isinstance(item, dict):
                continue
            finding_key = (
                str(item.get("check")),
                str(item.get("code")),
                str(item.get("message")),
            )
            if finding_key not in seen_findings:
                seen_findings.add(finding_key)
                findings.append(item)
    checks = sorted({str(item) for result in results for item in result.get("checks") or []})
    valid = (
        status == "completed"
        and not stale
        and not missing
        and not any(claim.get("status") in BLOCKING_CLAIM_STATUSES for claim in claims)
        and not error_findings({"findings": findings})
        and counts.get("supported", 0) > 0
    )
    return {
        "checks": checks,
        "findings": findings,
        "valid": valid,
        "status": status,
        "counts": counts,
        "claims": claims,
        "missing_evidence": missing,
        "stale_evidence": stale,
        "evidence_truncated": any(bool(result.get("evidence_truncated")) for result in results),
    }


# 回答を段落に分ける規則（RAG の `rag_engine.generation.operation_audit.answer_passages` と同じ。
# `rag_validate_answer` の `answer_quote` はこの段落の原文。製品をまたいで import しないため写す）。
_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")
_SENTENCE = re.compile(r"[^。]+(?:。[」』）)]*|$)")
_PASSAGE_CHARS = 600


def _passage_spans(line: str) -> list[tuple[int, int, str]]:
    """1 行の段落（行の中の開始・終了の位置と原文）。"""
    if _QUOTE_ONLY_LINE.fullmatch(line):
        pieces = [(0, len(line))]
    else:
        pieces = [(match.start(), match.end()) for match in _SENTENCE.finditer(line)]
    spans: list[tuple[int, int, str]] = []
    for start, end in pieces:
        raw = line[start:end]
        value = raw.strip()
        offset = start + len(raw) - len(raw.lstrip())
        for index in range(0, len(value), _PASSAGE_CHARS):
            text = value[index : index + _PASSAGE_CHARS]
            spans.append((offset + index, offset + index + len(text), text))
    return spans


def withhold_paragraphs(answer: str, quotes: set[str]) -> str | None:
    """回答から `quotes` の段落を外した本文（位置を決められない段落があれば None）。

    段落の位置は RAG と同じ規則で分けて決める（文字列の置換では、同じ文を含む別の段落まで
    削ってしまうため）。段落が無くなった行は消し、続く空行は 1 つにする。
    """
    found: set[str] = set()
    kept: list[str] = []
    for line in answer.split("\n"):
        spans = [span for span in _passage_spans(line) if span[2] in quotes]
        if not spans:
            kept.append(line)
            continue
        found.update(span[2] for span in spans)
        for start, end, _text in reversed(spans):
            line = line[:start] + line[end:]
        if line.strip():
            kept.append(line.rstrip())
    if quotes - found:
        return None
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def error_findings(result: JsonObject) -> list[JsonObject]:
    """決定的な検査の error の指摘（valid にしない。warning は確かめたい点なので出さない）。"""
    return [
        item
        for item in result.get("findings") or []
        if isinstance(item, dict) and item.get("severity") == "error"
    ]


def _notice_lines(result: JsonObject, claims: list[JsonObject]) -> list[str]:
    lines = [_claim_line(item) for item in claims[:_MAX_LISTED_CLAIMS]]
    if len(claims) > _MAX_LISTED_CLAIMS:
        lines.append(f"- ほか {len(claims) - _MAX_LISTED_CLAIMS} 件の主張")
    # RAG の決定的な検査（要求の漏れ・手順の順序と分岐・影響範囲。#1276）の error。
    findings = [item for item in error_findings(result) if item.get("message")]
    lines += [
        f"- {_short(item['message'], _MAX_FINDING_CHARS)}"
        for item in findings[:_MAX_LISTED_FINDINGS]
    ]
    if len(findings) > _MAX_LISTED_FINDINGS:
        lines.append(f"- ほか {len(findings) - _MAX_LISTED_FINDINGS} 件の指摘")
    stale = _refs_count(result.get("stale_evidence"))
    if stale:
        lines.append(f"- 根拠の {stale} 件は文書の古い版です。最新の版で確かめ直してください。")
    missing = _refs_count(result.get("missing_evidence"))
    if missing:
        lines.append(f"- 根拠の {missing} 件は見つかりません（削除された・参照できない）。")
    if not lines:
        lines.append("- 根拠で裏付けられた主張がありません。")
    return lines


def publish_answer(answer: str, result: JsonObject) -> tuple[str, JsonObject]:
    """判定（判定として使える結果）から、利用者に見せる回答と、外した内容の記録を返す。

    valid（または確かめる主張も error の指摘も無い）ならそのまま。そうでなければ、根拠で確かめられ
    なかった段落を外し、外した段落と理由・決定的な検査の error・古い版や見つからない根拠を
    「確かめられていない点」として足す。根拠を 1 件も読めなかった（`no_evidence`）とき、段落の位置を
    決められないとき、手順・影響範囲・業務ガイドの版の error があるとき（どの段落の手順が誤りかを
    決められず、確かめていない手順を出さない）は本文を載せない。要求の error（答えていない要求を
    示していない）は本文を残し、不足として示す。
    """
    errors = error_findings(result)
    if result.get("valid") is True or (result.get("status") == "no_claims" and not errors):
        return answer, {"claims": 0, "findings": 0, "all": False}
    blocking = [
        item
        for item in result.get("claims") or []
        if isinstance(item, dict) and item.get("status") in BLOCKING_CLAIM_STATUSES
    ]
    guide_errors = any(item.get("check") in WITHHOLDING_CHECKS for item in errors)
    body: str | None
    if result.get("status") == "no_evidence" or guide_errors:
        body = None
    elif blocking:
        quotes = {str(item.get("answer_quote") or "") for item in blocking}
        body = withhold_paragraphs(answer, quotes)
    else:
        body = answer.rstrip()
    lines = _notice_lines(result, blocking)
    if blocking or (body is None and not guide_errors):
        lines.insert(0, WITHHELD_INTRO)
    text = body or (GUIDE_WITHHELD if guide_errors else NOTHING_CONFIRMED)
    withheld = {"claims": len(blocking), "findings": len(errors), "all": not body}
    return f"{text}\n\n**{UNVERIFIED_HEADING}**\n\n" + "\n".join(lines), withheld


def combine_validations(answer: str, validations: list[JsonObject]) -> tuple[JsonObject, str]:
    """接続ごとの内容（`validation_content`）を 1 つにし、利用者に見せる回答を返す（#1277）。

    - どれかの接続を確かめられなかった（`unvalidated`）: 全体も `unvalidated` で、回答は消さずに
      「検証できませんでした」を足す（基盤の障害で内容を落とさない）。
    - すべての接続で判定が出た: 段落ごとにまとめた判定で回答を組み立てる（`publish_answer`）。
    """
    single = validations[0] if len(validations) == 1 else None
    common: JsonObject = {
        "connection": single["connection"] if single else None,
        "tool_name": single["tool_name"] if single else None,
        "step_id": single["step_id"] if single else None,
        "evidence": [ref for item in validations for ref in item["evidence"]],
    }
    failed = next((item for item in validations if item["status"] != STATUS_COMPLETED), None)
    if failed is not None:
        content = validation_content(
            STATUS_UNVALIDATED,
            reason=failed["reason"],
            message=failed["message"],
            result=single["result"] if single else None,
            **common,
        )
        published = with_unverified_notice(answer)
    else:
        merged = merge_results([item["result"] for item in validations])
        content = validation_content(STATUS_COMPLETED, result=merged, **common)
        published, content["withheld"] = publish_answer(answer, merged)
    content["connections"] = validations
    return content, published


def with_unverified_notice(answer: str) -> str:
    return f"{answer.rstrip()}\n\n{UNVERIFIED_NOTICE}"


def with_no_evidence_notice(answer: str) -> str:
    """資料の根拠を使わずに作った回答の末尾に、確かめていないことを足す（#1277）。"""
    return f"{answer.rstrip()}\n\n{NO_EVIDENCE_NOTICE}"
