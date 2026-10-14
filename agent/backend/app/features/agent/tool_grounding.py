"""根拠・データのツールを呼ばずに作った回答の拒答（#1835）。

業務 Agent が、資料の根拠のツール（RAG）や業務データのツール（NL2SQL・登録した MCP 接続）を
1 つも呼ばずに、モデルの知識だけで最終の回答を作ることがある。社内の資料・業務データで確かめて
いない回答は作り話の可能性があるため、利用者には出さない（ユーザーの決定、2026-10-14）。
プロンプトの指示だけでは拒答は安定しない（AbstentionBench など）ため、組み込み Runtime が
回答を確定する前に決定的に確かめる（Amazon Bedrock Guardrails の contextual grounding check の
BLOCK と同じ考え方）。

- 対象: この Run でモデルに根拠・データのツールを渡した業務 Agent の Run（チャット・自動実行・
  品質評価のどれも）。ツールを持たない Agent・利用者が資料の検索を取り消して根拠・データの
  ツールが残らない Run は対象外（ガードをすり抜けた回答は #1834 の「根拠を使わずに答えた」に出る）。
- 数える呼び出し: モデルが根拠・データのツールを呼んだ step（成功・失敗・承認の却下・評価の
  dry-run・自動実行で承認が要るため呼ばなかったものも、モデルが確かめようとしたので数える）。
  Control Plane が自分で呼んだもの（確認の取り消し・業務ガイドの照合・最終の検証）は数えない。
- 除外: 同じ会話の前の Run（会話の履歴の上限 `PLATFORM_CHAT_HISTORY_MAX_TURNS` の中）が根拠・
  データのツールで答えた Run（前の回答の言い換え・整形。事実の確かめは #1837 の最終の検証）と、
  利用者が確認を取り消した Run（「資料の検索は不要」など。#1441・#1445）と、
  利用者への確認の質問だけの回答（`is_clarification_only`）と、NL2SQL の SQL の生成前の確認で
  止まった Run（`nl2sql_clarification_pending`）。曖昧で対象が分からない質問は拒答ではなく確認に
  する（ユーザーの決定）。拒答の対象は、範囲外の質問と資料・データで確かめられない質問に限る。
- 1 回だけ差し戻す: 「業務の事実はツールで確かめてから答える。確かめられないなら拒答する」を
  利用者のメッセージとしてモデルへ返して続けさせる（自己修正）。それでも呼ばなければ、本文を
  出さず、決まった形の拒答の文（`refusal_text`）に置き換える。元の回答は Run の経過に残す。
- 差し戻しの文の曖昧な質問の扱いは、その Run でモデルに回答前の確認のツール
  （`ask_user_confirmation`）を渡したかで分ける（#1881）。渡したときは、まず資料・業務データの
  ツールで調べる（業務ガイド・NL2SQL が確認事項を返せば、それが確認のカードになる）か、
  `ask_user_confirmation` で確かめるよう伝え、本文に確認の質問を書かせない。渡していない（方針
  「前提を示して回答」・確認の上限・無人の Run）ときは、ツールを呼ばずに確認の質問だけを返して
  よいと伝える。差し戻しの後も、確認のツールを渡したのに本文で確認の質問だけを返した回答は、
  拒答にはせず、改善の手がかりとして Run の経過に残す（`clarified_in_text`）。
- 回答の対応は `insufficient_evidence`（basis `no_tool_grounding`）。最終の検証の成果物は
  `skipped`（reason `no_tool_grounding`）にする（資料の根拠を使わずに答えた `no_rag_evidence`
  とは分ける。#1834 のシグナルは拒答した Run を「答えられなかった」に数える）。

ここは判定と文だけを持つ（差し戻し・保存は `builtin_runtime`）。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, Any

from app.features.agent.answer_outcome import INSUFFICIENT_EVIDENCE, OUTCOME_SCHEMA_VERSION
from app.features.agent.answer_passages import is_clarification_only
from app.features.agent.clarification_cancel import CANCEL_TRACE_PREFIX
from app.features.agent.confirmation import CONFIRMATION_TOOL
from app.features.agent.support_task import (
    GUIDE_CHECK_TRACE_PREFIX,
    nl2sql_clarification_pending,
)
from app.features.agent.tools import MCP_TOOL_SEPARATOR, mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

# 回答の対応の basis と、最終の検証の成果物の reason。
BASIS_NO_TOOL_GROUNDING = "no_tool_grounding"
REASON_NO_TOOL_GROUNDING = "no_tool_grounding"
# Run の metadata に残す、差し戻しの記録（承認待ちから再開しても 2 回目を差し戻さない）。
METADATA_KEY = "tool_grounding"
# 差し戻しの回数（ユーザーの確認待ち。#1835）。
MAX_RETRIES = 1

# 根拠・データを確かめないツール（業務プロファイル・検索・回答プロファイルの一覧と推薦）と、
# Control Plane が自分で呼ぶツール（最終の検証・SQL の生成前の確認の取り消し）。
_NON_GROUNDING_TOOLS = frozenset(
    {
        "rag_list_search_answer_profiles",
        "rag_recommend_search_answer_profile",
        "rag_validate_answer",
        "nl2sql_list_profiles",
        "nl2sql_recommend_profile",
        "nl2sql_cancel_requirement",
    }
)
# Control Plane が自分で呼んだ step の trace_id の接頭辞（最終の検証は回答の確定の後なので
# ここでは現れないが、念のため数えない）。
_CONTROL_PLANE_TRACE_PREFIXES = (
    CANCEL_TRACE_PREFIX,
    GUIDE_CHECK_TRACE_PREFIX,
    "answer_validation_",
)

_RETRY_HEAD = (
    "直前の回答は、業務の事実をツールで確かめずに作られています。そのままでは利用者に出せません。"
    "業務の事実（規程・手順・数値・状態・記録など）は、資料の根拠のツールと業務データのツールの"
    "結果だけで答えてください。質問に答えるのに要るツールを呼んで確かめてから、改めて回答してください。"
)
_RETRY_TAIL = (
    "ツールで確かめられないとき・この業務の範囲外の質問のときは、推測や一般的な知識で答えず、"
    "確かめられないため答えられないと短く答えてください。"
)
# 回答前の確認のツールを渡していない Run（方針「前提を示して回答」・確認の上限・無人の Run）。
RETRY_MESSAGE = (
    _RETRY_HEAD
    + "質問が曖昧で対象（文書・システム・期間など）が分からないときは、ツールを呼ばずに、"
    "対象を確かめる確認の質問だけを返してかまいません（質問の文だけにし、例・説明・答えた後の"
    "案内は添えない）。" + _RETRY_TAIL
)
# 回答前の確認のツール（ask_user_confirmation）を渡した Run（#1881）。本文で聞き返させず、資料・
# 業務データのツールの確認事項か、確認のツールのカードで確かめさせる。
RETRY_MESSAGE_CONFIRMABLE = (
    _RETRY_HEAD
    + "質問が曖昧に見えるときも、まず資料・業務データのツールで調べてください（業務ガイド・資料・"
    "業務データのツールが確認事項を返せば、それが利用者への確認のカードになります）。"
    "ツールの結果を見ても対象（文書・システム・期間など）が決まらないときと、プロファイルの推薦の"
    "候補が割れたときだけ、"
    f"{CONFIRMATION_TOOL} で確認してください（選択肢は候補の名前と説明）。"
    "本文に確認の質問や選ばせる表を書かないでください。" + _RETRY_TAIL
)

REFUSAL_HEAD = (
    "社内の資料・業務データで確かめられなかったため、お答えできません。推測ではお答えしていません。"
)
_RAG_EXAMPLE = "「経費精算規程の締め日は？」"
_DATA_EXAMPLE = "「先月の部門別の受注金額は？」"
_SCOPE_DESCRIPTION_MAX_CHARS = 200


def is_grounding_tool(name: str) -> bool:
    """根拠・データのツールか（MCP 接続のツール。一覧・推薦・Control Plane が呼ぶものを除く）。

    Control Plane のツール（`agent_skill_list`・`skill_reference_read` など。名前に接続の
    区切りが無い）は数えない。
    """
    if MCP_TOOL_SEPARATOR not in name:
        return False
    base = mcp_base_tool_name(name)
    return bool(base) and base not in _NON_GROUNDING_TOOLS


def grounding_tools(tool_names: Iterable[str]) -> list[str]:
    """モデルに渡したツールのうち、根拠・データのツール。"""
    return [name for name in tool_names if is_grounding_tool(name)]


def grounding_attempted(steps: list[RunStep]) -> bool:
    """モデルがこの Run で根拠・データのツールを呼んだ（呼ぼうとした）か。"""
    for step in steps:
        call = step.tool_call
        if call is None or not is_grounding_tool(call.name):
            continue
        if (call.trace_id or "").startswith(_CONTROL_PLANE_TRACE_PREFIXES):
            continue
        return True
    return False


def grounding_used(steps: list[RunStep]) -> bool:
    """モデルが根拠・データのツールを呼んで結果を得たか（成功した呼び出しだけ）。"""
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not is_grounding_tool(call.name):
            continue
        if (call.trace_id or "").startswith(_CONTROL_PLANE_TRACE_PREFIXES):
            continue
        if result.success and step.status == "completed":
            return True
    return False


def grounded_in_thread(previous_steps: Iterable[list[RunStep]]) -> bool:
    """同じ会話の前の Run（会話の履歴の上限の中）のどれかが、根拠・データのツールで答えたか。

    そうなら、今の Run がツールを呼ばずに前の回答を言い換え・整形・要約しても拒答にしない
    （「前の回答を表にして」「もっと短く」など）。前のターンの事実の確かめは、前のターンで
    裏付けた根拠を今のターンの最終の検証に渡す仕組み（#1837）に任せる（今のターンに根拠が無くても、
    その根拠だけで検証する。#1879）。
    """
    return any(grounding_used(steps) for steps in previous_steps)


def is_exempt_answer(answer: str, steps: list[RunStep]) -> bool:
    """拒答の対象外の回答か（利用者への確認の質問だけ・NL2SQL の SQL の生成前の確認で止まった）。"""
    return is_clarification_only(answer) or nl2sql_clarification_pending(steps)


def needs_grounding(answer: str, *, steps: list[RunStep], tool_names: Collection[str]) -> bool:
    """ツールで確かめていない回答として、差し戻し・拒答の対象か。"""
    if not grounding_tools(tool_names):
        return False
    if grounding_attempted(steps):
        return False
    return not is_exempt_answer(answer, steps)


def clarified_in_text(answer: str, *, steps: list[RunStep], tool_names: Collection[str]) -> bool:
    """回答前の確認のツールを渡したのに、ツールを呼ばずに本文で確認の質問だけを返したか（#1881）。

    拒答にはしない（`is_exempt_answer`）。確認のカードが出なかった改善の手がかりとして、
    呼ぶ側が Run の経過に残す。
    """
    if CONFIRMATION_TOOL not in tool_names or not grounding_tools(tool_names):
        return False
    if grounding_attempted(steps) or nl2sql_clarification_pending(steps):
        return False
    return is_clarification_only(answer)


def retry_message(*, confirmable: bool) -> str:
    """差し戻しの文（回答前の確認のツールを渡したかで、曖昧な質問の扱いを分ける。#1881）。"""
    return RETRY_MESSAGE_CONFIRMABLE if confirmable else RETRY_MESSAGE


# 差し戻しに添える元の回答の長さの上限（モデルへの入力を増やしすぎない）。
_RETRY_ANSWER_MAX_CHARS = 4000


def retry_input(items: list[Any], answer: str, *, confirmable: bool = False) -> list[Any]:
    """差し戻して Run を続ける入力（前の入力・ツールの呼び出し・差し戻しの利用者のメッセージ）。

    前の応答の assistant のメッセージ（`output_text`）は入力に入れない。OCI Enterprise AI の
    `openai.gpt-oss-120b` は、`input` の `output_text` を「output_text is not supported by model」の
    400 で拒否する（2026-10-14 に実環境で確認）。その直前の reasoning item も、続く出力が無くなる
    ため外す。元の回答は差し戻しのメッセージの中に引用として添える（何を直すかをモデルに示す）。
    `confirmable` は、この Run でモデルに `ask_user_confirmation` を渡したか（`retry_message`）。
    """
    kept: list[Any] = []
    for item in items:
        data = item if isinstance(item, dict) else None
        if data is None and hasattr(item, "model_dump"):
            data = item.model_dump(exclude_none=True)
        if isinstance(data, dict) and data.get("role") == "assistant":
            while kept and _item_type(kept[-1]) == "reasoning":
                kept.pop()
            continue
        kept.append(item)
    quoted = answer.strip()
    if len(quoted) > _RETRY_ANSWER_MAX_CHARS:
        quoted = quoted[: _RETRY_ANSWER_MAX_CHARS - 1] + "…"
    head = retry_message(confirmable=confirmable)
    message = f"{head}\n\n直前の回答（利用者には出していません）:\n{quoted}"
    return [*kept, {"role": "user", "content": message}]


def _item_type(item: Any) -> object:
    if isinstance(item, dict):
        return item.get("type")
    return getattr(item, "type", None)


def retried(metadata: object) -> int:
    """この Run で差し戻した回数。"""
    record = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    count = record.get("retries") if isinstance(record, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0


def _scope_line(description: str, skill_names: list[str]) -> str:
    text = " ".join(description.split())
    if len(text) > _SCOPE_DESCRIPTION_MAX_CHARS:
        text = text[: _SCOPE_DESCRIPTION_MAX_CHARS - 1] + "…"
    names = "、".join(dict.fromkeys(name.strip() for name in skill_names if name.strip()))
    if text and names:
        return f"この業務 Agent が扱う内容: {text}（スキル: {names}）"
    if text or names:
        return f"この業務 Agent が扱う内容: {text or names}"
    return ""


def refusal_text(
    *, description: str = "", skill_names: list[str] | None = None, tool_names: Collection[str] = ()
) -> str:
    """拒答の文（決まった形。何が無いか・扱う範囲・質問し直し方を示し、推測で補わない）。

    言い換えの例は、モデルに渡した根拠・データのツールの種類から選ぶ（資料なら規程の質問、
    業務データなら期間と集計の質問。どちらでもなければ両方）。
    """
    grounding = grounding_tools(tool_names)
    has_rag = any(mcp_base_tool_name(name).startswith("rag_") for name in grounding)
    has_data = any(not mcp_base_tool_name(name).startswith("rag_") for name in grounding)
    examples = [
        example
        for example, used in ((_RAG_EXAMPLE, has_rag), (_DATA_EXAMPLE, has_data))
        if used or not (has_rag or has_data)
    ]
    lines = [REFUSAL_HEAD]
    scope = _scope_line(description, list(skill_names or []))
    if scope:
        lines.append(scope)
    lines.append(
        "この業務 Agent が扱う内容について、文書名・システム名・期間などを入れて質問してください。"
        f"例: {''.join(examples)}"
    )
    return "\n\n".join(lines)


def refusal_outcome() -> JsonObject:
    """拒答の回答の対応（`answer_outcome` と同じ形）。"""
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "value": INSUFFICIENT_EVIDENCE,
        "basis": BASIS_NO_TOOL_GROUNDING,
        "rag_outcome": None,
        "signals": [],
    }


__all__ = [
    "BASIS_NO_TOOL_GROUNDING",
    "MAX_RETRIES",
    "METADATA_KEY",
    "REASON_NO_TOOL_GROUNDING",
    "REFUSAL_HEAD",
    "RETRY_MESSAGE",
    "RETRY_MESSAGE_CONFIRMABLE",
    "clarified_in_text",
    "grounding_attempted",
    "grounded_in_thread",
    "grounding_tools",
    "grounding_used",
    "is_exempt_answer",
    "is_grounding_tool",
    "needs_grounding",
    "refusal_outcome",
    "refusal_text",
    "retried",
    "retry_input",
    "retry_message",
]
