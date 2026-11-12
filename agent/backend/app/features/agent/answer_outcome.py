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

1. `empty_answer`: 回答が空 → insufficient_evidence
2. `nl2sql_clarification`: 最後の `nl2sql_query` が SQL の生成前の確認の項目を返した
   → needs_clarification
   `clarification_question`: 確認の質問だけの回答（`is_clarification_only`）→ needs_clarification
3. `rag_search`: 回答が最後の `rag_search` に拠る（その後に根拠を集め直していない）とき、その対応。
   現場のデータが要る（needs_environment_data）を現場のデータの道具（RAG 以外の MCP 接続のツール。
   #1283。業務プロファイルの一覧・推薦は除く。#108）で確かめたら answered / conditional
   （`environment_tools`）、確認の質問を求められたが質問だけで答えなかったら conditional、
   answered でも下の「不足の印」があれば conditional にする。
4. `guide_handoff`: `rag_search` に拠らず、最も新しい `rag_lookup_guides` の最上位の業務ガイドの
   判断が人への引き継ぎ（handoff）→ needs_human
5. `answer_passages`（または現場のデータの道具を使ったなら `environment_tools`）: Agent が自分で
   組み立てた回答。不足の印があれば conditional、無ければ answered。

不足の印（`signals`）: 業務ガイドの条件が分からないまま答えた印（`guide_conditions`。最後の
`rag_search` の後の、最も新しい `rag_lookup_guides` の最上位の業務ガイドの判断が、条件を確かめる
（clarify）・条件ごとに答える（branch）なのに、確認の質問だけで答えなかった。#1321・#1322）・回答に
使った章節を終わりまで読めず、「読み切れていない章節」を示した印（`unread_sections`。#2492）。

記録だけのシグナル（対応の値を変えない。#3546）: RAG の根拠のツールが縮退した検索の結果を返した
（`rag_degraded`。任意の補助の処理の縮退 `degraded_steps` か安全チェックの未完了
`guardrail_degraded`。任意の能力の縮退は回答を続ける契約なので不足の印にしない）。

ここは決め方だけを持つ（呼び出しと保存は `builtin_runtime`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import is_clarification_only
from app.features.agent.clarification_cancel import CANCEL_TRACE_PREFIX
from app.features.agent.job_cancel import RUN_CANCEL_TRACE_PREFIX
from app.features.agent.rag_retrieval_state import degraded_retrieval
from app.features.agent.support_task import (
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
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
BASIS_RAG_SEARCH = "rag_search"
BASIS_ENVIRONMENT_TOOLS = "environment_tools"
BASIS_GUIDE_HANDOFF = "guide_handoff"
BASIS_ANSWER_PASSAGES = "answer_passages"

SIGNAL_QUESTION = "question"
SIGNAL_ENVIRONMENT_TOOLS = "environment_tools"
SIGNAL_GUIDE_CONDITIONS = "guide_conditions"
# 回答に使った章節を終わりまで読めず、回答に「読み切れていない章節」を示した（#2492）。
SIGNAL_UNREAD_SECTIONS = "unread_sections"
# RAG の根拠のツールが縮退した検索の結果を返した（不足の印にしない記録。#3546）。
SIGNAL_RAG_DEGRADED = "rag_degraded"
# 回答が不足・条件を示している印（answered を conditional にする）。
_GAP_SIGNALS = frozenset({SIGNAL_GUIDE_CONDITIONS, SIGNAL_UNREAD_SECTIONS})
_RAG_LOOKUP_GUIDES = "rag_lookup_guides"
# 業務ガイドの判断のうち、利用者の分かっていない条件に答えが拠るもの（#1321・#1322）。
_GUIDE_CONDITIONAL_DECISIONS = frozenset({"clarify", "branch"})


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


def _rag_evidence_outputs(steps: list[RunStep]) -> list[dict[str, Any]]:
    """成功した RAG の根拠のツール（`rag_search` / `rag_retrieve_evidence`）の結果。"""
    outputs: list[dict[str, Any]] = []
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if not _succeeded(step) or call is None or result is None:
            continue
        if mcp_base_tool_name(call.name) not in {RAG_SEARCH, RAG_RETRIEVE_EVIDENCE}:
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
) -> JsonObject:
    """最終の回答の対応（成果物 `answer` の `outcome`）。

    `answer` はモデルの最終の回答。返す内容は `value`（対応）・`basis`（決めた手がかり）・
    `rag_outcome`（最後の `rag_search` の対応。呼んでいなければ None）・`signals`（不足の印）。
    `coverage` は回答に使った章節を終わりまで読んだか（`section_coverage`。#2492）で、読めて
    いなければ不足の印 `unread_sections` にする。
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
    signals: set[str] = set()
    if any(degraded_retrieval(output) for output in _rag_evidence_outputs(steps)):
        signals.add(SIGNAL_RAG_DEGRADED)
    # 業務ガイドが条件を確かめる・条件ごとに答えると判断したのに、確認の質問だけで答えなかった
    # （最後の rag_search の後の照合だけを見る。rag_search の対応は下で扱う。#1321・#1322）。
    after_search = last_search[0] + 1 if last_search is not None else 0
    if _guide_decision(steps[after_search:]) in _GUIDE_CONDITIONAL_DECISIONS:
        signals.add(SIGNAL_GUIDE_CONDITIONS)
    if isinstance(coverage, dict) and coverage.get("status") == "incomplete":
        signals.add(SIGNAL_UNREAD_SECTIONS)
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
    return _content(
        CONDITIONAL if gaps else ANSWERED, basis, rag_outcome=rag_outcome, signals=signals
    )


__all__ = [
    "ANSWER_OUTCOMES",
    "OUTCOME_SCHEMA_VERSION",
    "answer_outcome",
    "environment_tool_calls",
]
