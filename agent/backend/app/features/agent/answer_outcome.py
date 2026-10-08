"""回答の対応（outcome）の決定（#1305）。

業務 Agent の最終の回答に、RAG の回答の記録（AnswerEnvelope。#1235）と同じ語彙の対応を付けて、
成果物 `answer` の `outcome` に残す。業務支援の評価（#1289 の D）が、回答の文からの推定に頼らずに
A / C（RAG の回答の記録の `outcome`）と同じ尺度で採点するため。

対応はモデルを呼ばずに、Run の step（ツールの引数と結果）・最終の検証の結果（`answer_validation`）・
回答の段落の判定（`answer_passages`）から決定的に決める。モデルに対応を申告させる（構造化出力）と、
回答の形が Agent ごとに変わり、申告と本文が食い違ったときの扱いも要るため、ここでは使わない。

語彙（RAG の `AnswerOutcome` と同じ）:

- answered: 答えた（条件・不足を示していない）
- conditional: 条件・不足・確かめられていない点を示して答えた
- needs_clarification: 利用者に条件を確かめた（確認の質問だけの回答）
- needs_environment_data: 現場の値・記録の確認が要ると示した
- needs_human: 人への引き継ぎを示した
- insufficient_evidence: 資料から答えられないと示した（拒答）

決め方（上から順に、最初に当たったもの。`basis` に残す）:

1. `empty_answer`: 回答が空 → insufficient_evidence
2. `clarification_question`: 確認の質問だけの回答（`is_clarification_only`）→ needs_clarification
3. `validation_withheld_all`: 最終の検証が本文をすべて載せなかった → insufficient_evidence
4. `rag_search`: 回答が最後の `rag_search` に拠る（その後に根拠を集め直していない）とき、
   その対応。ただし、現場のデータが要る（needs_environment_data）を現場のデータの道具（RAG 以外の
   MCP 接続のツール。#1283）で確かめたら answered / conditional（`environment_tools`）、確認の
   質問を求められたが質問だけで答えなかったら conditional、answered でも回答が不足を示して
   いれば（下の「不足の印」）conditional にする。
5. `guide_handoff`: `rag_search` に拠らず、最も新しい `rag_lookup_guides` の最上位の業務ガイドの
   判断が人への引き継ぎ（handoff）→ needs_human
6. `answer_passages`（または現場のデータの道具を使ったなら `environment_tools`）: Agent が自分で
   組み立てた回答。主張の段落（最終の検証で外した段落と、実データの確認を促すだけの段落を除く）が
   無ければ、実データの確認を促していれば needs_environment_data、それ以外は insufficient_evidence。
   主張があり、実データの確認を促すか不足の印があれば conditional、無ければ answered。

不足の印（`signals`）: 資料に記載が無い文・拒答の文（`absence`）・主張に添えた利用者への質問
（`question`）・回答の中の「確かめられていない点」の節（`unverified_section`）・最終の検証が
根拠で確かめられない・根拠と矛盾すると判定して外した段落（`withheld_claims`。`unsupported` /
`contradicted`。確かめが終わらなかった `unassessed` などだけなら付けない。#1317）・要求の不足などの
決定的な検査の error（`check_errors`）・実データの確認を促す段落（`data_confirmation`。RAG の
`rag_validate_answer` の判定か、利用者に現場のデータ・記録の確認を求める段落の決定的な判定
`environment_check_passages`。#1317。外した段落は数えない）。

ここは決め方だけを持つ（呼び出しと保存は `builtin_runtime`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import (
    KIND_ABSENCE,
    KIND_QUESTION,
    KIND_UNVERIFIED,
    environment_check_passages,
    is_clarification_only,
    non_claim_passages,
    passage_spans,
)
from app.features.agent.answer_validation import BLOCKING_CLAIM_STATUSES, STATUS_COMPLETED
from app.features.agent.support_task import (
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
    is_environment_tool,
)
from app.features.agent.tools import mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

OUTCOME_SCHEMA_VERSION = 1

ANSWERED = "answered"
CONDITIONAL = "conditional"
NEEDS_CLARIFICATION = "needs_clarification"
NEEDS_ENVIRONMENT_DATA = "needs_environment_data"
NEEDS_HUMAN = "needs_human"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
# RAG の `AnswerOutcome`（AnswerEnvelope。#1235）と同じ語彙。
ANSWER_OUTCOMES = frozenset(
    {
        ANSWERED,
        CONDITIONAL,
        NEEDS_CLARIFICATION,
        NEEDS_ENVIRONMENT_DATA,
        NEEDS_HUMAN,
        INSUFFICIENT_EVIDENCE,
    }
)

BASIS_EMPTY_ANSWER = "empty_answer"
BASIS_CLARIFICATION = "clarification_question"
BASIS_WITHHELD_ALL = "validation_withheld_all"
BASIS_RAG_SEARCH = "rag_search"
BASIS_ENVIRONMENT_TOOLS = "environment_tools"
BASIS_GUIDE_HANDOFF = "guide_handoff"
BASIS_ANSWER_PASSAGES = "answer_passages"

SIGNAL_ABSENCE = "absence"
SIGNAL_QUESTION = "question"
SIGNAL_UNVERIFIED_SECTION = "unverified_section"
SIGNAL_WITHHELD_CLAIMS = "withheld_claims"
SIGNAL_CHECK_ERRORS = "check_errors"
SIGNAL_DATA_CONFIRMATION = "data_confirmation"
SIGNAL_ENVIRONMENT_TOOLS = "environment_tools"
# 回答が不足・条件を示している印（answered を conditional にする）。
_GAP_SIGNALS = frozenset(
    {
        SIGNAL_ABSENCE,
        SIGNAL_QUESTION,
        SIGNAL_UNVERIFIED_SECTION,
        SIGNAL_WITHHELD_CLAIMS,
        SIGNAL_CHECK_ERRORS,
        SIGNAL_DATA_CONFIRMATION,
    }
)
_RAG_LOOKUP_GUIDES = "rag_lookup_guides"
# RAG の `rag_validate_answer` の、実データの確認を促すだけの段落の判定。
_DATA_CONFIRMATION = "data_confirmation"
# 不足の印（`withheld_claims`）にする、外した段落の判定（根拠で確かめられない・根拠と矛盾）。
# 確かめが終わらなかった段落（`unassessed`。出典の行・表の区切りなど）を外しただけでは、回答が
# 条件・不足を示したことにならない（#1317）。外した段落は本文に載らないので主張にも数えない。
_DISPUTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
_KIND_SIGNALS = {
    KIND_ABSENCE: SIGNAL_ABSENCE,
    KIND_QUESTION: SIGNAL_QUESTION,
    KIND_UNVERIFIED: SIGNAL_UNVERIFIED_SECTION,
}


def _succeeded(step: RunStep) -> bool:
    return (
        step.tool_call is not None
        and step.tool_result is not None
        and step.tool_result.success
        and step.status == "completed"
    )


def _last_rag_search(steps: list[RunStep]) -> tuple[int, str] | None:
    """最後に成功した `rag_search` の位置と対応（対応が語彙に無ければ None）。"""
    for index in range(len(steps) - 1, -1, -1):
        step = steps[index]
        if not _succeeded(step) or step.tool_call is None or step.tool_result is None:
            continue
        if mcp_base_tool_name(step.tool_call.name) != RAG_SEARCH:
            continue
        output = step.tool_result.output
        outcome = output.get("outcome") if isinstance(output, dict) else None
        return (index, outcome) if isinstance(outcome, str) and outcome in ANSWER_OUTCOMES else None
    return None


def _retrieved_after(steps: list[RunStep], index: int) -> bool:
    """`index` の後に、根拠を集め直した（`rag_retrieve_evidence` が成功した）か。"""
    return any(
        _succeeded(step)
        and step.tool_call is not None
        and mcp_base_tool_name(step.tool_call.name) == RAG_RETRIEVE_EVIDENCE
        for step in steps[index + 1 :]
    )


def _environment_resolved(steps: list[RunStep], start: int = 0) -> bool:
    """`start` 以降に、現場のデータの道具（RAG 以外の MCP 接続のツール）が成功したか。"""
    return any(
        _succeeded(step) and step.tool_call is not None and is_environment_tool(step.tool_call.name)
        for step in steps[start:]
    )


def _guide_decision(steps: list[RunStep]) -> str | None:
    """最も新しい `rag_lookup_guides` の最上位の業務ガイドの判断。"""
    for step in reversed(steps):
        if not _succeeded(step) or step.tool_call is None or step.tool_result is None:
            continue
        if mcp_base_tool_name(step.tool_call.name) != _RAG_LOOKUP_GUIDES:
            continue
        output = step.tool_result.output
        guides = output.get("guides") if isinstance(output, dict) else None
        if not isinstance(guides, list) or not guides or not isinstance(guides[0], dict):
            return None
        decision = guides[0].get("decision")
        return decision if isinstance(decision, str) else None
    return None


def _validation_parts(
    validation: JsonObject | None,
) -> tuple[set[str], set[str], set[str], JsonObject]:
    """最終の検証から（外した段落, そのうち確かめられない・矛盾の段落, 実データの確認を促す段落,
    外した数）を取り出す。

    判定が出た（`completed`）ときだけ使う（確かめられなかった検証の段落の判定は使わない）。
    """
    if not isinstance(validation, dict) or validation.get("status") != STATUS_COMPLETED:
        return set(), set(), set(), {}
    withheld = validation.get("withheld")
    withheld = withheld if isinstance(withheld, dict) else {}
    result = validation.get("result")
    claims = result.get("claims") if isinstance(result, dict) else None
    removed: set[str] = set()
    disputed: set[str] = set()
    confirmations: set[str] = set()
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict) or claim.get("non_claim"):
            continue
        quote = str(claim.get("answer_quote") or "")
        if not quote:
            continue
        status = claim.get("status")
        if status == _DATA_CONFIRMATION:
            confirmations.add(quote)
        elif status in BLOCKING_CLAIM_STATUSES and _count(withheld.get("claims")) > 0:
            removed.add(quote)
            if status in _DISPUTED_CLAIM_STATUSES:
                disputed.add(quote)
    return removed, disputed, confirmations, withheld


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _passages(answer: str) -> tuple[list[str], set[str]]:
    """主張の段落と、主張ではない段落の印（不足・質問・確かめられていない点の節）。"""
    kinds = non_claim_passages(answer)
    claims: list[str] = []
    signals: set[str] = set()
    for line in answer.split("\n"):
        for _start, _end, text in passage_spans(line):
            kind = kinds.get(text)
            if kind is None:
                claims.append(text)
            elif kind in _KIND_SIGNALS:
                signals.add(_KIND_SIGNALS[kind])
    return claims, signals


def _content(outcome: str, basis: str, *, rag_outcome: str | None, signals: set[str]) -> JsonObject:
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "value": outcome,
        "basis": basis,
        "rag_outcome": rag_outcome,
        "signals": sorted(signals),
    }


def answer_outcome(
    answer: str, *, steps: list[RunStep], validation: JsonObject | None = None
) -> JsonObject:
    """最終の回答の対応（成果物 `answer` の `outcome`）。

    `answer` はモデルの最終の回答（最終の検証で段落を外す前の本文）、`validation` は最終の
    検証の成果物の内容（検証しなかった Run は None）。返す内容は `value`（対応）・`basis`
    （決めた手がかり）・`rag_outcome`（最後の `rag_search` の対応。呼んでいなければ None）・
    `signals`（不足の印）。
    """
    last_search = _last_rag_search(steps)
    rag_outcome = last_search[1] if last_search else None
    if not answer.strip():
        return _content(
            INSUFFICIENT_EVIDENCE, BASIS_EMPTY_ANSWER, rag_outcome=rag_outcome, signals=set()
        )
    if is_clarification_only(answer):
        return _content(
            NEEDS_CLARIFICATION,
            BASIS_CLARIFICATION,
            rag_outcome=rag_outcome,
            signals={SIGNAL_QUESTION},
        )
    removed, disputed, confirmations, withheld = _validation_parts(validation)
    claims, signals = _passages(answer)
    if withheld.get("all") is True:
        return _content(
            INSUFFICIENT_EVIDENCE, BASIS_WITHHELD_ALL, rag_outcome=rag_outcome, signals=signals
        )
    # 利用者に現場のデータ・記録の確認を求める段落（検証の判定が supported でも。#1317）。
    # 外した段落は利用者に見えないので数えない。
    confirmations |= environment_check_passages(answer) - removed
    if disputed:
        signals.add(SIGNAL_WITHHELD_CLAIMS)
    if _count(withheld.get("findings")) > 0:
        signals.add(SIGNAL_CHECK_ERRORS)
    if confirmations:
        signals.add(SIGNAL_DATA_CONFIRMATION)
    gaps = bool(signals & _GAP_SIGNALS)

    if last_search is not None and not _retrieved_after(steps, last_search[0]):
        index, outcome = last_search
        if outcome == NEEDS_ENVIRONMENT_DATA and _environment_resolved(steps, index + 1):
            signals.add(SIGNAL_ENVIRONMENT_TOOLS)
            # 現場のデータの確認を求められて確かめた。確かめる点が残っていれば不足。
            return _content(
                CONDITIONAL if gaps else ANSWERED,
                BASIS_ENVIRONMENT_TOOLS,
                rag_outcome=rag_outcome,
                signals=signals,
            )
        if outcome == NEEDS_CLARIFICATION:
            # 確認を求められたが、質問だけでなく答えた（分岐ごとに答えたなど）。
            outcome = CONDITIONAL
        elif outcome == ANSWERED and gaps:
            outcome = CONDITIONAL
        return _content(outcome, BASIS_RAG_SEARCH, rag_outcome=rag_outcome, signals=signals)

    if last_search is None and _guide_decision(steps) == "handoff":
        return _content(NEEDS_HUMAN, BASIS_GUIDE_HANDOFF, rag_outcome=rag_outcome, signals=signals)

    environment = _environment_resolved(steps)
    if environment:
        signals.add(SIGNAL_ENVIRONMENT_TOOLS)
    basis = BASIS_ENVIRONMENT_TOOLS if environment else BASIS_ANSWER_PASSAGES
    substantive = [text for text in claims if text not in removed and text not in confirmations]
    if not substantive:
        if confirmations and not environment:
            outcome = NEEDS_ENVIRONMENT_DATA
        elif confirmations:
            outcome = CONDITIONAL
        else:
            outcome = INSUFFICIENT_EVIDENCE
        return _content(outcome, basis, rag_outcome=rag_outcome, signals=signals)
    return _content(
        CONDITIONAL if gaps else ANSWERED, basis, rag_outcome=rag_outcome, signals=signals
    )


__all__ = [
    "ANSWER_OUTCOMES",
    "OUTCOME_SCHEMA_VERSION",
    "answer_outcome",
]
