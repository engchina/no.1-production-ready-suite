"""回答の最終の検証（#1246、handoff §12）。

呼び出し側（Agent など）が渡した根拠の参照（document_id・chunk_id）を、今の利用者の権限と今の版で
読み直し（呼び出し側の本文は信じない）、回答の主張を rag_engine の監査（モデル 1 回）で確かめる。

呼び出し側が AnswerEnvelope の要求や業務ガイドの参照を渡したときは、決定的な検査（`answer_checks`。
モデルは呼ばない）も行い、結果を findings として主張の監査と並べて返す（#1276）。業務ガイドは
呼び出し側の内容を信じず、今の利用者の権限で検索・回答プロファイルを確かめ、公開の版をストアから読み直す。
error の finding があれば valid にしない。

呼び出し側が `rag_search` の回答の記録（trace_id）を渡したときは、その記録を今の利用者の範囲で
読み、監査を通った説明と本文が同じ段落は、根拠を今の権限と版で読み直して本文が変わって
いなければ、その判定を使ってモデルに渡さない（#1527。すべての段落が再利用できればモデルを
呼ばない）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.clients.oracle import OracleClient
from app.clients.support_guide_store import SupportGuideStore
from app.config import Settings
from app.rag.answer_checks import (
    AnswerFinding,
    check_guide_steps,
    check_impact,
    check_requests,
)
from app.rag.answer_engine import (
    ANSWER_CITATION_LINES_KEY,
    _sheet_location,
    build_engine_settings,
)
from app.rag.answer_threads import run_answer_in_thread

# 主張の監査で「検証に通った」とみなさない判定。
_BLOCKING_STATUSES = frozenset({"unsupported", "contradicted", "citation_error", "unassessed"})


@dataclass(frozen=True)
class EvidenceRef:
    document_id: str
    chunk_id: str


@dataclass(frozen=True)
class GuideCheckRef:
    """回答が沿った業務ガイド（検索・回答プロファイル・guide_id・公開の版・分かっている条件）。"""

    search_answer_profile_id: str
    guide_id: str
    revision: int
    conditions: Mapping[str, str] = field(default_factory=dict)


class GuideProfileNotFoundError(LookupError):
    """業務ガイドの検索・回答プロファイルが無いか、利用できる範囲の外。"""


@dataclass
class AnswerValidation:
    status: str
    valid: bool
    claim_checks: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    missing_evidence: list[EvidenceRef] = field(default_factory=list)
    stale_evidence: list[EvidenceRef] = field(default_factory=list)
    evidence_truncated: bool = False
    # 決定的な検査（#1276）。checks は行った検査（requests / guide_steps / impact）。
    findings: list[AnswerFinding] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    # 検査に使った業務ガイドの公開の版（業務ガイドを渡さなければ None）。
    guide_revision: int | None = None
    # RAG の回答の監査の判定を再利用した段落の数（#1527。モデルに渡さなかった段落）。
    reused_claims: int = 0


def evidence_location(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """検証に渡す根拠の場所（節と、表計算のシート・行・セル範囲）。

    値は MCP の根拠の location と同じ（#1450）。

    段落が書いた「行23」「シート「台帳」」などの場所を、モデルが根拠の場所と照らせるようにする。
    分からない項目は入れない（頁は呼び出し側が ``page_start``・``page_end`` で渡す）。
    """
    location: dict[str, Any] = {}
    section = metadata.get("section_path")
    if isinstance(section, str):
        section = section.split(" > ")
    if isinstance(section, list):
        path = [str(item).strip() for item in section if str(item).strip()]
        if path:
            location["section_path"] = path
    sheet = _sheet_location(metadata)
    if sheet is not None:
        location.update(sheet)
    return location


def is_valid(status: str, counts: dict[str, int], *, unreadable: int) -> bool:
    """矛盾・裏付けの無い主張・読めない根拠が無く、裏付けのある主張が 1 つ以上あるか。"""
    if status != "completed" or unreadable:
        return False
    if any(counts.get(item, 0) for item in _BLOCKING_STATUSES):
        return False
    return counts.get("supported", 0) > 0


async def _guide_findings(
    oracle: OracleClient, answer: str, guide: GuideCheckRef
) -> tuple[list[AnswerFinding], list[str], int | None]:
    """業務ガイドの公開の版を読み直し、手順・影響範囲を確かめる。

    ストアの障害は例外のまま上げる（「業務ガイドが無い」と区別する。handoff §10）。
    """
    view = await oracle.get_search_answer_profile(guide.search_answer_profile_id)
    if view is None:
        raise GuideProfileNotFoundError(guide.search_answer_profile_id)
    published = await SupportGuideStore(oracle).published_contents(view.id)
    found = next(
        ((revision, content) for gid, revision, content in published if gid == guide.guide_id),
        None,
    )
    if found is None:
        return (
            [
                AnswerFinding(
                    "guide",
                    "guide_unavailable",
                    "error",
                    "業務ガイドが公開されていないか、アーカイブされています。",
                )
            ],
            ["guide"],
            None,
        )
    revision, content = found
    findings: list[AnswerFinding] = []
    if revision != guide.revision:
        # 今の公開の版で確かめ、版が変わったことを示す（回答は作り直しが要る）。
        findings.append(
            AnswerFinding(
                "guide",
                "guide_revision_stale",
                "error",
                f"業務ガイドの版 {guide.revision} は古い版です（今の公開の版は {revision}）。",
            )
        )
    findings += check_guide_steps(answer, content, guide.conditions)
    findings += check_impact(answer, content, guide.conditions)
    return findings, ["guide", "guide_steps", "impact"], revision


# 監査の判定を再利用する回答の記録の数の上限（契約の maxItems）。
MAX_ANSWER_TRACE_IDS = 10


async def audited_answer_lines(
    oracle: OracleClient,
    trace_ids: Sequence[str],
    readable: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    """回答の記録の、監査を通った説明の本文 → 根拠の chunk の id（#1527。`audited_answer_items`）。

    記録は今の利用者の範囲で読む（持ち主・利用できる検索・回答プロファイル。読めない記録は使わない）。
    根拠は ``readable``（今の権限と版で読み直した chunk。id → 検証に渡す根拠）にあり、記録の
    引用の本文と変わっていないものだけを使う。監査が時間の上限で切れた回答（#1516）の説明は
    監査されていないので使わない。
    """
    from rag_engine.evaluation.answer_validation import audited_answer_items

    audited: dict[str, list[str]] = {}
    for trace_id in list(dict.fromkeys(trace_ids))[:MAX_ANSWER_TRACE_IDS]:
        row = await oracle.get_answer_record(trace_id)
        if row is None:
            continue
        diagnostics = row.get("diagnostics_json")
        if not isinstance(diagnostics, Mapping):
            continue
        budget = diagnostics.get("round_budget")
        if isinstance(budget, Mapping) and budget.get("adopted_audit_cut"):
            continue
        envelope = diagnostics.get("envelope")
        items = envelope.get("items") if isinstance(envelope, Mapping) else None
        citations = row.get("citations_json")
        if not isinstance(items, list) or not isinstance(citations, list):
            continue
        stored: dict[str, str] = {}
        lines: dict[str, list[int]] = {}
        for citation in citations:
            if not isinstance(citation, Mapping):
                continue
            chunk_id = str(citation.get("chunk_id") or "")
            metadata = citation.get("metadata")
            numbers = (
                metadata.get(ANSWER_CITATION_LINES_KEY) if isinstance(metadata, Mapping) else None
            )
            if chunk_id and isinstance(numbers, list):
                lines[chunk_id] = [int(n) for n in numbers if isinstance(n, int)]
                stored[chunk_id] = str(citation.get("text") or "")
        for key, chunk_ids in audited_answer_items(
            str(row.get("answer") or ""), items, lines
        ).items():
            # 根拠を今読めて、記録したときから本文が変わっていない（版・権限の変化を
            # 読み直しで確かめる）。
            if all(
                chunk_id in readable
                and str(readable[chunk_id].get("text") or "") == stored.get(chunk_id)
                for chunk_id in chunk_ids
            ):
                audited.setdefault(key, chunk_ids)
    return audited


async def validate_answer(
    question: str,
    answer: str,
    refs: Sequence[EvidenceRef],
    settings: Settings,
    *,
    oracle: OracleClient | None = None,
    requests: Sequence[Mapping[str, str]] | None = None,
    gaps: Sequence[str] = (),
    guide: GuideCheckRef | None = None,
    answer_trace_ids: Sequence[str] = (),
) -> AnswerValidation:
    oracle = oracle or OracleClient()
    # 決定的な検査を先に行う（業務ガイドを読めなければ、モデルを呼ぶ前に止める）。
    findings: list[AnswerFinding] = []
    checks: list[str] = []
    guide_revision: int | None = None
    if requests is not None:
        findings += check_requests(answer, requests, gaps)
        checks.append("requests")
    if guide is not None:
        guide_items, guide_checks, guide_revision = await _guide_findings(oracle, answer, guide)
        findings += guide_items
        checks += guide_checks
    blocking = any(item.severity == "error" for item in findings)
    deterministic: dict[str, Any] = {
        "findings": findings,
        "checks": checks,
        "guide_revision": guide_revision,
    }
    items: list[dict[str, Any]] = []
    missing: list[EvidenceRef] = []
    stale: list[EvidenceRef] = []
    for ref in dict.fromkeys(refs):
        chunk = await oracle.retrievable_chunk(ref.document_id, ref.chunk_id)
        if chunk is None:
            exists = await oracle.accessible_chunk_exists(ref.document_id, ref.chunk_id)
            (stale if exists else missing).append(ref)
            continue
        metadata = dict(chunk.metadata)
        items.append(
            {
                "id": chunk.chunk_id,
                "source": chunk.file_name or chunk.document_id,
                "page_start": metadata.get("page_start"),
                "page_end": metadata.get("page_end"),
                **evidence_location(metadata),
                "text": chunk.text,
            }
        )
    if not items:
        return AnswerValidation(
            status="no_evidence",
            valid=False,
            missing_evidence=missing,
            stale_evidence=stale,
            **deterministic,
        )

    # RAG の回答の監査を通った説明と本文・根拠が同じ段落は、その判定を使う（#1527）。
    audited = (
        await audited_answer_lines(oracle, answer_trace_ids, {item["id"]: item for item in items})
        if answer_trace_ids
        else {}
    )

    def run() -> dict[str, Any]:
        from rag_engine.evaluation.answer_validation import validate_answer_claims

        with tempfile.TemporaryDirectory(prefix="rag-engine-validate-") as work:
            engine_settings = build_engine_settings(settings, output_dir=Path(work))
            return validate_answer_claims(question, answer, items, engine_settings, audited=audited)

    # 回答の専用の thread で動かし、呼び出し側の打ち切り・取り消しを thread に伝える(#1422)。
    result = await run_answer_in_thread(run)
    counts = {str(key): int(value) for key, value in (result.get("counts") or {}).items()}
    status = str(result.get("status") or "error")
    return AnswerValidation(
        status=status,
        valid=is_valid(status, counts, unreadable=len(missing) + len(stale)) and not blocking,
        claim_checks=list(result.get("claim_checks") or []),
        counts=counts,
        missing_evidence=missing,
        stale_evidence=stale,
        evidence_truncated=bool(result.get("evidence_truncated")),
        reused_claims=len(result.get("reused_passage_ids") or ()),
        **deterministic,
    )


__all__ = [
    "AnswerValidation",
    "EvidenceRef",
    "GuideCheckRef",
    "GuideProfileNotFoundError",
    "evidence_location",
    "is_valid",
    "validate_answer",
]
