"""回答の対応（outcome）の決定（#1305）。

業務 Agent の最終の回答に、RAG の回答の記録（AnswerEnvelope。#1235）と同じ語彙の対応を付けて、
成果物 `answer` の `outcome` に残す。業務支援の評価（#1289 の D）が、回答の文からの推定に頼らずに
A / C（RAG の回答の記録の `outcome`）と同じ尺度で採点するため。

対応はモデルを呼ばずに、Run の step（ツールの引数と結果）と実行の成否から決定的に決める。回答の
語の形では決めない（#4714）。

語彙（RAG の `AnswerOutcome` と同じ）:

- answered: 答えた（条件・不足を示していない）
- conditional: 条件・不足を示して答えた
- needs_clarification: 利用者に条件を確かめた（確認の質問だけの回答）
- needs_environment_data: 現場の値・記録の確認が要ると示した
- needs_human: 人への引き継ぎを示した
- insufficient_evidence: 資料から答えられないと示した（拒答）

根拠・データのツールを呼ばずに作った回答を拒答の文に置き換えた Run（#1835）は、ここを通さずに
`tool_grounding.refusal_outcome`（insufficient_evidence、basis `no_tool_grounding`。ツールで調べずに
範囲外・答えられないと判断した拒答は `declined_without_tools`。#2608）を残す。

決め方（上から順に、最初に当たったもの。`basis` に残す）:

1. `approved_faq_candidates`: 最後の RAG の回答のツール（`rag_search` / `rag_chat`）が
   登録済みQ&Aの候補だけを返した（利用者が選ぶ確認のカードになる。検索はしていないので、
   失敗・根拠不足ではない。#5134・#5136）→ needs_clarification
   `empty_answer`: 回答が空 → insufficient_evidence
2. `nl2sql_clarification`: 最後の `nl2sql_query` が SQL の生成前の確認の項目を返した
   → needs_clarification
3. `rag_search` / `rag_chat`（呼んだツールの名前）: RAG の回答をそのまま Run の回答にした
   （スキルが回答のツールを宣言した Run。#5342）→ そのツールの対応のまま。
   `clarification_question`: 確認の質問だけの回答 → needs_clarification
4. `rag_search` / `rag_chat`: この Run で RAG の回答のツールを 1 回だけ呼んだ（#5342）とき、その
   対応。現場のデータが要る（needs_environment_data）を現場のデータの道具（RAG 以外の MCP 接続の
   ツール。#1283。業務プロファイルの一覧・推薦は除く。#108）で確かめたら answered / conditional
   （`environment_tools`）、確認の質問を求められたが質問だけで答えなかったら conditional、
   answered でも下の「不足の印」があれば conditional にする。
5. `answer_passages`（現場のデータの道具を使ったなら `environment_tools`、利用者が選んだ登録済みQ&A
   の回答を受け取ったなら `approved_faq`。#5136）: RAG の回答のツールを複数回呼んだ・呼んで
   いない回答（Agent の回答）。不足の印があれば conditional、無ければ answered。

不足の印（`signals`）: 回答に使った章節を終わりまで読めず、「読み切れていない章節」を示した印
（`unread_sections`。#2492）。

記録だけのシグナル（対応の値を変えない。#3546）: RAG の回答のツールが縮退した検索の結果を返した
（`rag_degraded`。任意の補助の処理の縮退 `degraded_steps` か安全チェックの未完了
`guardrail_degraded`。任意の能力の縮退は回答を続ける契約なので不足の印にしない）・RAG が利用者の
選んだ登録済みQ&Aを返した（`approved_faq`。根拠が登録済みQ&A。#5136）。

ここは決め方だけを持つ（呼び出しと保存は `builtin_runtime`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import is_clarification_only
from app.features.agent.clarification_cancel import CANCEL_TRACE_PREFIX
from app.features.agent.job_cancel import RUN_CANCEL_TRACE_PREFIX
from app.features.agent.rag_retrieval_state import degraded_retrieval
from app.features.agent.support_task import (
    RAG_ANSWER_TOOLS,
    approved_faq_candidates_step,
    chosen_approved_faq,
    is_approved_faq_candidates,
    is_environment_data_tool,
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
BASIS_APPROVED_FAQ_CANDIDATES = "approved_faq_candidates"
BASIS_ENVIRONMENT_TOOLS = "environment_tools"
BASIS_ANSWER_PASSAGES = "answer_passages"
# 利用者が選んだ登録済みQ&Aを根拠にした回答（#5136）。
BASIS_APPROVED_FAQ = "approved_faq"

SIGNAL_QUESTION = "question"
SIGNAL_ENVIRONMENT_TOOLS = "environment_tools"
# 回答に使った章節を終わりまで読めず、回答に「読み切れていない章節」を示した（#2492）。
SIGNAL_UNREAD_SECTIONS = "unread_sections"
# RAG の根拠のツールが縮退した検索の結果を返した（不足の印にしない記録。#3546）。
SIGNAL_RAG_DEGRADED = "rag_degraded"
# RAG の検索が利用者の選んだ登録済みQ&Aを返した（根拠が登録済みQ&A。記録だけ。#5136）。
SIGNAL_APPROVED_FAQ = "approved_faq"
# 回答が不足・条件を示している印（answered を conditional にする）。
_GAP_SIGNALS = frozenset({SIGNAL_UNREAD_SECTIONS})


def _succeeded(step: RunStep) -> bool:
    return (
        step.tool_call is not None
        and step.tool_result is not None
        and step.tool_result.success
        and step.status == "completed"
    )


def _rag_answer_calls(steps: list[RunStep]) -> list[tuple[int, str, str | None]]:
    """成功した RAG の回答のツールの呼び出し（位置・ツールの素の名前・対応。対応が語彙に無ければ
    None）。登録済みQ&Aの候補だけを返した呼び出し（回答していない）は数えない。"""
    calls: list[tuple[int, str, str | None]] = []
    for index, step in enumerate(steps):
        if not _succeeded(step) or step.tool_call is None or step.tool_result is None:
            continue
        tool = mcp_base_tool_name(step.tool_call.name)
        if tool not in RAG_ANSWER_TOOLS:
            continue
        output = step.tool_result.output
        if is_approved_faq_candidates(output):
            continue
        outcome = output.get("outcome") if isinstance(output, dict) else None
        calls.append(
            (
                index,
                tool,
                outcome if isinstance(outcome, str) and outcome in ANSWER_OUTCOMES else None,
            )
        )
    return calls


def _environment_call(step: RunStep) -> bool:
    """現場のデータの道具（RAG 以外の MCP 接続のツール）の成功した呼び出しか。

    Control Plane が確認の取り消しで閉じた SQL の生成前の確認
    （`nl2sql_cancel_requirement`。#1441）は、現場のデータを確かめた呼び出しではないので数えない。
    業務プロファイルの一覧・推薦（`nl2sql_list_profiles` など。使える業務データを探すだけ）も、
    値・記録を確かめていないので数えない（空の一覧の成功で範囲外の拒答が `environment_tools` の
    条件付きの回答になっていた。#108。拒答の段階 `refusal_stage` と同じ定義）。
    """
    return (
        _succeeded(step)
        and step.tool_call is not None
        and is_environment_data_tool(step.tool_call.name)
        and not (step.tool_call.trace_id or "").startswith(
            (CANCEL_TRACE_PREFIX, RUN_CANCEL_TRACE_PREFIX)
        )
    )


def _environment_resolved(steps: list[RunStep], start: int = 0) -> bool:
    """`start` 以降に、現場のデータの道具が成功したか。"""
    return any(_environment_call(step) for step in steps[start:])


def environment_tool_calls(steps: list[RunStep]) -> int:
    """現場のデータの道具（NL2SQL など RAG 以外の MCP 接続のツール）の成功した呼び出しの数。

    確かめるべき実行の「根拠を使わずに答えた」（#1834）が、業務データの道具で答えた Run を
    除くのに使う
    （回答の対応の `environment_tools` と同じ判定）。
    """
    return sum(1 for step in steps if _environment_call(step))


def _rag_evidence_outputs(steps: list[RunStep]) -> list[dict[str, Any]]:
    """成功した RAG の回答のツール（`rag_search` / `rag_chat`）の結果。"""
    outputs: list[dict[str, Any]] = []
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if not _succeeded(step) or call is None or result is None:
            continue
        if mcp_base_tool_name(call.name) not in RAG_ANSWER_TOOLS:
            continue
        if isinstance(result.output, dict):
            outputs.append(result.output)
    return outputs


def _content(outcome: str, basis: str, *, rag_outcome: str | None, signals: set[str]) -> JsonObject:
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "value": outcome,
        "basis": basis,
        "rag_outcome": rag_outcome,
        "signals": sorted(signals),
    }


def answer_outcome(
    answer: str,
    *,
    steps: list[RunStep],
    coverage: JsonObject | None = None,
    rag_answer: bool = False,
) -> JsonObject:
    """最終の回答の対応（成果物 `answer` の `outcome`）。

    `answer` は Run の最終の回答。返す内容は `value`（対応）・`basis`（決めた手がかり）・
    `rag_outcome`（最後の RAG の回答のツールの対応。呼んでいなければ None）・`signals`（不足の印）。
    `coverage` は回答に使った章節を終わりまで読んだか（`section_coverage`。#2492）で、読めて
    いなければ不足の印 `unread_sections` にする。`rag_answer` は RAG の回答をそのまま Run の回答に
    したか（#5342）。
    """
    calls = _rag_answer_calls(steps)
    rag_outcome = calls[-1][2] if calls else None
    if approved_faq_candidates_step(steps) is not None:
        # 候補を利用者が選ぶ確認のカードが答え（回答の本文が空でも、失敗・根拠不足ではない）。
        return _content(
            NEEDS_CLARIFICATION,
            BASIS_APPROVED_FAQ_CANDIDATES,
            rag_outcome=rag_outcome,
            signals={SIGNAL_QUESTION},
        )
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
    signals: set[str] = set()
    evidence_outputs = _rag_evidence_outputs(steps)
    if any(degraded_retrieval(output) for output in evidence_outputs):
        signals.add(SIGNAL_RAG_DEGRADED)
    approved_faq = any(chosen_approved_faq(output) is not None for output in evidence_outputs)
    if approved_faq:
        signals.add(SIGNAL_APPROVED_FAQ)
    if rag_answer and calls:
        # RAG の回答をそのまま返した（確認の問いの回答も RAG の対応のまま）。
        _index, tool, outcome = calls[-1]
        if outcome == NEEDS_CLARIFICATION:
            signals.add(SIGNAL_QUESTION)
        return _content(outcome or ANSWERED, tool, rag_outcome=rag_outcome, signals=signals)
    if is_clarification_only(answer):
        return _content(
            NEEDS_CLARIFICATION,
            BASIS_CLARIFICATION,
            rag_outcome=rag_outcome,
            signals=signals | {SIGNAL_QUESTION},
        )
    if isinstance(coverage, dict) and coverage.get("status") == "incomplete":
        signals.add(SIGNAL_UNREAD_SECTIONS)
    gaps = bool(signals & _GAP_SIGNALS)

    index, tool, single = calls[0] if len(calls) == 1 else (0, "", None)
    if single is not None:
        if single == NEEDS_ENVIRONMENT_DATA and _environment_resolved(steps, index + 1):
            signals.add(SIGNAL_ENVIRONMENT_TOOLS)
            # 現場のデータの確認を求められて確かめた。確かめる点が残っていれば不足。
            return _content(
                CONDITIONAL if gaps else ANSWERED,
                BASIS_ENVIRONMENT_TOOLS,
                rag_outcome=rag_outcome,
                signals=signals,
            )
        if single == NEEDS_CLARIFICATION:
            # 確認を求められたが、質問だけでなく答えた（分岐ごとに答えたなど）。
            single = CONDITIONAL
        elif single == ANSWERED and gaps:
            single = CONDITIONAL
        return _content(single, tool, rag_outcome=rag_outcome, signals=signals)

    environment = _environment_resolved(steps)
    if environment:
        signals.add(SIGNAL_ENVIRONMENT_TOOLS)
    if environment:
        basis = BASIS_ENVIRONMENT_TOOLS
    else:
        basis = BASIS_APPROVED_FAQ if approved_faq else BASIS_ANSWER_PASSAGES
    return _content(
        CONDITIONAL if gaps else ANSWERED, basis, rag_outcome=rag_outcome, signals=signals
    )


__all__ = [
    "ANSWER_OUTCOMES",
    "OUTCOME_SCHEMA_VERSION",
    "answer_outcome",
    "environment_tool_calls",
]
