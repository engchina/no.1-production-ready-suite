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

根拠・データのツールを呼ばずに作った回答を拒答の文に置き換えた Run（#1835）は、ここを通さずに
`tool_grounding.refusal_outcome`（insufficient_evidence、basis `no_tool_grounding`）を残す。

決め方（上から順に、最初に当たったもの。`basis` に残す）:

1. `empty_answer`: 回答が空 → insufficient_evidence
2. `nl2sql_clarification`: 最後の `nl2sql_query` が AI要件確認の項目を返した（SQL を作らずに
   利用者に確かめた。#1441）→ needs_clarification
   `clarification_question`: 確認の質問だけの回答（`is_clarification_only`）→ needs_clarification
3. `validation_withheld_guide`: 最終の検証が、業務ガイドの手順・影響範囲・版の検査の error で
   本文をすべて載せなかった（どの段落の手順が誤りかを決められない）→ needs_human（人が手順を
   確かめる。#1491）
   `validation_withheld_all`: それ以外で最終の検証が本文をすべて載せなかった → insufficient_evidence
4. `rag_search`: 回答が最後の `rag_search` に拠る（その後に根拠を集め直していない）とき、
   その対応。ただし、現場のデータが要る（needs_environment_data）を現場のデータの道具（RAG 以外の
   MCP 接続のツール。#1283）で確かめたら answered / conditional（`environment_tools`）、確認の
   質問を求められたが質問だけで答えなかったら conditional、answered でも回答が不足を示して
   いれば（下の「不足の印」）conditional にする。
5. `guide_handoff`: `rag_search` に拠らず、最も新しい `rag_lookup_guides` の最上位の業務ガイドの
   判断が人への引き継ぎ（handoff）→ needs_human
6. `absence_conclusion`: 現場のデータの道具を使っておらず、Run のどれかの `rag_search` が
   insufficient_evidence を返し、回答の最初の段落（結論。見出し・出典を除く）が資料から確かめられ
   ないと述べた → insufficient_evidence（調べた資料の一覧・近い規定の紹介が続いても。#1454）
7. `answer_passages`（または現場のデータの道具を使ったなら `environment_tools`）: Agent が自分で
   組み立てた回答。主張の段落（最終の検証で外した段落と、実データの確認を促すだけの段落を除く）が
   無ければ、実データの確認を促していれば needs_environment_data、それ以外は insufficient_evidence。
   主張があり、実データの確認を促すか不足の印があれば conditional、無ければ answered。

不足の印（`signals`）: 資料に記載が無い文・拒答の文（`absence`。ただし、結論の段落が質問に答え
〔補足の外の主張に、最終の検証が根拠で確かめた段落がある〕、不足の文が補足の段落〔見出しが「補足」
「注記」などの節と「なお、」で始まる段落〕にだけあるときは、質問が求めない周辺の事柄の不足として
不足の印にせず、記録の `supplement_absence` だけを付ける。#1475）・主張に添えた利用者への質問
（`question`）・回答の中の「確かめられていない点」の節（`unverified_section`。最終の検証が
本文を残して確かめきれなかった点を示したときも。#1391）・最終の検証が
根拠で確かめられない・根拠と矛盾すると判定して外した段落（`withheld_claims`。`unsupported` /
`contradicted`。確かめが終わらなかった `unassessed` などだけなら付けない。#1317。外した段落が補足の
段落にだけあり、結論の段落〔補足の外の主張〕に最終の検証が根拠で確かめた段落があるときは、質問が
求めない周辺の事柄を外しただけなので不足の印にせず、記録の `supplement_withheld` だけを付ける。
#1491）・要求の不足などの
決定的な検査の error（`check_errors`）・実データの確認を促す段落（`data_confirmation`。RAG の
`rag_validate_answer` の判定か、利用者に現場のデータ・記録の確認を求める段落の決定的な判定
`environment_check_passages`。#1317。外した段落は数えない）・分岐ごとに答えた印（`branches`。
「…の場合」の見出し・ラベルが 2 つ以上。利用者の条件を確かめずに分岐ごとに答えた。#1317）・
業務ガイドの条件が分からないまま答えた印（`guide_conditions`。最後の `rag_search` の後の、最も新しい
`rag_lookup_guides`（モデルか Control Plane の照合。#1322）の最上位の業務ガイドの判断が、条件を
確かめる（clarify）・条件ごとに答える（branch）なのに、確認の質問だけで答えなかった。回答の前提が
利用者の分かっていない条件に拠るので、条件付きの回答（conditional）にする。`rag_search` が確認を
求めたのに答えたときと同じ扱い。#1321・#1322）。

ここは決め方だけを持つ（呼び出しと保存は `builtin_runtime`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import (
    KIND_ABSENCE,
    KIND_QUESTION,
    KIND_UNVERIFIED,
    case_labels,
    environment_check_passages,
    is_absence_conclusion,
    is_clarification_only,
    non_claim_passages,
    passage_spans,
    supplement_passages,
)
from app.features.agent.answer_validation import (
    BLOCKING_CLAIM_STATUSES,
    STATUS_COMPLETED,
    WITHHOLDING_CHECKS,
    error_findings,
)
from app.features.agent.clarification_cancel import CANCEL_TRACE_PREFIX
from app.features.agent.support_task import (
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
    is_environment_tool,
    nl2sql_clarification_pending,
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
BASIS_NL2SQL_CLARIFICATION = "nl2sql_clarification"
BASIS_WITHHELD_ALL = "validation_withheld_all"
# 業務ガイドの手順・影響範囲・版の検査の error で、最終の検証が本文をすべて外した（#1491）。
BASIS_WITHHELD_GUIDE = "validation_withheld_guide"
BASIS_RAG_SEARCH = "rag_search"
BASIS_ENVIRONMENT_TOOLS = "environment_tools"
BASIS_GUIDE_HANDOFF = "guide_handoff"
BASIS_ANSWER_PASSAGES = "answer_passages"
# 回答の結論（最初の段落）が「資料から確かめられない」で、rag_search が insufficient_evidence を
# 返した（#1454）。
BASIS_ABSENCE_CONCLUSION = "absence_conclusion"

SIGNAL_ABSENCE = "absence"
SIGNAL_QUESTION = "question"
SIGNAL_UNVERIFIED_SECTION = "unverified_section"
SIGNAL_WITHHELD_CLAIMS = "withheld_claims"
SIGNAL_CHECK_ERRORS = "check_errors"
SIGNAL_DATA_CONFIRMATION = "data_confirmation"
SIGNAL_ENVIRONMENT_TOOLS = "environment_tools"
SIGNAL_BRANCHES = "branches"
SIGNAL_GUIDE_CONDITIONS = "guide_conditions"
# 結論の段落で答えた後の、補足の段落だけの不足の文（不足の印にしない記録。#1475）。
SIGNAL_SUPPLEMENT_ABSENCE = "supplement_absence"
# 結論の段落で答えた後の、補足の段落だけを最終の検証が外した（不足の印にしない記録。#1491）。
SIGNAL_SUPPLEMENT_WITHHELD = "supplement_withheld"
# 回答が不足・条件を示している印（answered を conditional にする）。
_GAP_SIGNALS = frozenset(
    {
        SIGNAL_BRANCHES,
        SIGNAL_GUIDE_CONDITIONS,
        SIGNAL_ABSENCE,
        SIGNAL_QUESTION,
        SIGNAL_UNVERIFIED_SECTION,
        SIGNAL_WITHHELD_CLAIMS,
        SIGNAL_CHECK_ERRORS,
        SIGNAL_DATA_CONFIRMATION,
    }
)
_RAG_LOOKUP_GUIDES = "rag_lookup_guides"
# 業務ガイドの判断のうち、利用者の分かっていない条件に答えが拠るもの（#1321・#1322）。
_GUIDE_CONDITIONAL_DECISIONS = frozenset({"clarify", "branch"})
# RAG の `rag_validate_answer` の、実データの確認を促すだけの段落の判定。
_DATA_CONFIRMATION = "data_confirmation"
# 不足の印（`withheld_claims`）にする、外した段落の判定（根拠で確かめられない・根拠と矛盾）。
# 確かめが終わらなかった段落（`unassessed`。出典の行・表の区切りなど）を外しただけでは、回答が
# 条件・不足を示したことにならない（#1317）。外した段落は本文に載らないので主張にも数えない。
_DISPUTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
# 結論の段落を探すときに飛ばす、主張ではない段落の種類（見出し・出典・表の形）。
_LEAD_SKIPPED_KINDS = frozenset({"heading", "citation", "table"})
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
    """`start` 以降に、現場のデータの道具（RAG 以外の MCP 接続のツール）が成功したか。

    Control Plane が確認の取り消しで閉じた AI要件確認（`nl2sql_cancel_requirement`。#1441）は、
    現場のデータを確かめた呼び出しではないので数えない。
    """
    return any(
        _succeeded(step)
        and step.tool_call is not None
        and is_environment_tool(step.tool_call.name)
        and not (step.tool_call.trace_id or "").startswith(CANCEL_TRACE_PREFIX)
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


def _supported_quotes(validation: JsonObject | None) -> set[str]:
    """最終の検証が根拠で確かめた（`supported`）段落（判定が出た `completed` のときだけ）。"""
    if not isinstance(validation, dict) or validation.get("status") != STATUS_COMPLETED:
        return set()
    result = validation.get("result")
    claims = result.get("claims") if isinstance(result, dict) else None
    return {
        str(claim.get("answer_quote") or "")
        for claim in (claims if isinstance(claims, list) else [])
        if isinstance(claim, dict)
        and not claim.get("non_claim")
        and claim.get("status") == "supported"
    }


def _passages(answer: str) -> tuple[list[str], set[str], set[str]]:
    """主張の段落と、主張ではない段落の印（不足・質問・確かめられていない点の節）と、不足の文。"""
    kinds = non_claim_passages(answer)
    claims: list[str] = []
    signals: set[str] = set()
    absences: set[str] = set()
    for line in answer.split("\n"):
        for _start, _end, text in passage_spans(line):
            kind = kinds.get(text)
            if kind is None:
                claims.append(text)
            elif kind in _KIND_SIGNALS:
                signals.add(_KIND_SIGNALS[kind])
                if kind == KIND_ABSENCE:
                    absences.add(text)
    return claims, signals, absences


def _only_in_supplement(
    answer: str, *, claims: list[str], passages: set[str], answered: set[str]
) -> bool:
    """結論の段落が質問に答え、`passages`（不足の文・外した段落）が補足の段落にだけあるか。

    結論の段落（補足の外の主張）に、最終の検証が根拠で確かめた（`supported`）段落があるときを
    「質問に答えた」とみなす（`answered` は外した段落と実データの確認を促す段落を除いた `supported`
    の段落）。補足の「現在の各環境の設定値は資料からは分かりません。」のような不足の文（#1475）や、
    最終の検証が外した補足の「なお、…の資料は見当たりませんでした。」（#1491）は、質問が求めない
    周辺の事柄で、答えの不足ではない。結論の段落の不足の文（資料に答えが無いことを結論にした回答。
    #1454）・結論の段落を外したときは今までどおり不足の印にする。最終の検証が判定を出さなかった
    回答は変えない。
    """
    supplement = supplement_passages(answer)
    if not passages or not passages <= supplement:
        return False
    return any(text in answered for text in claims if text not in supplement)


def _guide_withheld(validation: JsonObject | None) -> bool:
    """最終の検証が、業務ガイドの手順・影響範囲・版の検査の error を出したか（#1491）。"""
    if not isinstance(validation, dict):
        return False
    result = validation.get("result")
    return isinstance(result, dict) and any(
        item.get("check") in WITHHOLDING_CHECKS for item in error_findings(result)
    )


def _rag_insufficient(steps: list[RunStep]) -> bool:
    """Run のどれかの `rag_search` が insufficient_evidence を返したか（#1454）。"""
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if not _succeeded(step) or call is None or result is None:
            continue
        output = result.output
        if (
            mcp_base_tool_name(call.name) == RAG_SEARCH
            and isinstance(output, dict)
            and output.get("outcome") == INSUFFICIENT_EVIDENCE
        ):
            return True
    return False


def _concludes_absence(answer: str) -> bool:
    """回答の最初の段落（見出し・出典・表の形を除く結論）が、資料から確かめられないと述べるか。"""
    kinds = non_claim_passages(answer)
    for line in answer.split("\n"):
        for _start, _end, text in passage_spans(line):
            if not text.strip() or kinds.get(text) in _LEAD_SKIPPED_KINDS:
                continue
            return kinds.get(text) == KIND_ABSENCE or is_absence_conclusion(text)
    return False


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
    if nl2sql_clarification_pending(steps):
        return _content(
            NEEDS_CLARIFICATION,
            BASIS_NL2SQL_CLARIFICATION,
            rag_outcome=rag_outcome,
            signals={SIGNAL_QUESTION},
        )
    if is_clarification_only(answer):
        return _content(
            NEEDS_CLARIFICATION,
            BASIS_CLARIFICATION,
            rag_outcome=rag_outcome,
            signals={SIGNAL_QUESTION},
        )
    removed, disputed, confirmations, withheld = _validation_parts(validation)
    claims, signals, absences = _passages(answer)
    if withheld.get("all") is True:
        if _guide_withheld(validation):
            # 手順・影響範囲の検査で本文を載せなかった: 資料が無いのではなく、どの段落の手順が
            # 誤りかを決められない。人が業務ガイドと照らして確かめる（#1491）。
            signals.add(SIGNAL_CHECK_ERRORS)
            return _content(
                NEEDS_HUMAN, BASIS_WITHHELD_GUIDE, rag_outcome=rag_outcome, signals=signals
            )
        return _content(
            INSUFFICIENT_EVIDENCE, BASIS_WITHHELD_ALL, rag_outcome=rag_outcome, signals=signals
        )
    # 利用者に現場のデータ・記録の確認を求める段落（検証の判定が supported でも。#1317）。
    # 外した段落は利用者に見えないので数えない。
    confirmations |= environment_check_passages(answer) - removed
    # 結論の段落で答えた後の、補足の段落だけの不足の文は不足の印にしない（#1475）。
    answered = _supported_quotes(validation) - removed - confirmations
    if _only_in_supplement(answer, claims=claims, passages=absences, answered=answered):
        signals.discard(SIGNAL_ABSENCE)
        signals.add(SIGNAL_SUPPLEMENT_ABSENCE)
    if disputed:
        # 補足の段落だけを外したときは、答えの不足ではない（#1491）。
        supplement_only = _only_in_supplement(
            answer, claims=claims, passages=disputed, answered=answered
        )
        signals.add(SIGNAL_SUPPLEMENT_WITHHELD if supplement_only else SIGNAL_WITHHELD_CLAIMS)
    # 利用者の条件を確かめずに、分岐ごとに答えた（rag_search の確認の求めに分岐で答えたのと同じ）。
    if len(case_labels(answer)) >= 2:
        signals.add(SIGNAL_BRANCHES)
    # 業務ガイドが条件を確かめる・条件ごとに答えると判断したのに、確認の質問だけで答えなかった
    # （最後の rag_search の後の照合だけを見る。rag_search の対応は下で扱う。#1321・#1322）。
    after_search = last_search[0] + 1 if last_search is not None else 0
    if _guide_decision(steps[after_search:]) in _GUIDE_CONDITIONAL_DECISIONS:
        signals.add(SIGNAL_GUIDE_CONDITIONS)
    if _count(withheld.get("findings")) > 0:
        signals.add(SIGNAL_CHECK_ERRORS)
    # 本文を残して、確かめきれなかった点（出典を照合できない段落など）を示した（#1391）。
    if _count(withheld.get("unverified")) > 0:
        signals.add(SIGNAL_UNVERIFIED_SECTION)
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
    elif _rag_insufficient(steps) and _concludes_absence(answer):
        # 質問の対象が資料に無いと結論した回答（#1454）。調べた資料の一覧・近い規定の紹介が
        # 続いても答えなし（参考の説明は本文に残す）。
        return _content(
            INSUFFICIENT_EVIDENCE,
            BASIS_ABSENCE_CONCLUSION,
            rag_outcome=rag_outcome,
            signals=signals,
        )
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
