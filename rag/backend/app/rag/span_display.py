"""工程の span の表示用のデータ（処理の経過は trace の投影。#3226）。

処理の経過の行は span（`pr_backend_core.observability.tracing`）で、工程を測る span に
表示用のデータ（`SpanDisplay`: 種類 `kind`・名前の値 `params`・開いて見る詳細
`details`）を付ける。種類と値は、画面の工程の定義（`frontend/src/lib/chat-progress.ts`
の `CHAT_PROGRESS_DEFINITIONS`）が名前・計測値を付けるときの key で、次の 2 つにする。

- 回答フローの工程（`rag_engine` の `_execution_step` の span `rag.engine.stage`、
  資料との照合・承認済み FAQ からの回答）: 種類 ``answer_step``。値は工程名 ``name``・
  LLM の回数 ``llm_calls``（送り直しで送信が多ければ ``llm_sends``）・結果の説明
  ``note``（「該当なし」など）。
- 回答フローの外の工程（会話を踏まえた質問の書き換え・質問から条件の読み取り・
  回答の安全チェック・回答の記録の保存）: 工程の名前（``history_rewrite`` など）を
  種類にする。

全体を包む工程（`answer` / `retrieval`）と技術の span（モデルの呼び出し・埋め込み・
並べ替え）は種類を付けず、画面が共通の規則（段の名前・技術の span の名前）で名前を付ける。
"""

from __future__ import annotations

from collections.abc import Mapping

from pr_backend_core.chat_progress import ChatProgressStepDetails, chat_progress_details

from app.rag.step_details import step_details_model

# 回答フローの工程の種類（値の ``name`` が工程名）。
ANSWER_STEP_KIND = "answer_step"

# 回答フローの外の工程のうち、種類を付けるもの（工程の名前を種類にする）。
PIPELINE_STEP_KINDS: frozenset[str] = frozenset(
    {"history_rewrite", "field_filter", "answer_guardrail", "answer_record_save"}
)

# 工程の失敗の詳細の行（段を開いて見る。利用者向けの短い要約とコード。#2342）。
_STEP_FAILURE_SUMMARY: Mapping[str, str] = {
    "answer_record_save": "回答の記録をデータベースに保存できませんでした。回答は表示しています。",
}
_STEP_FAILURE_DEFAULT = "この工程でエラーが発生しました。"

# 回答の記録の工程の状態のうち、結果の説明ではないもの（完了・例外で中断）。
_PLAIN_STEP_STATUSES: frozenset[str] = frozenset({"", "complete", "エラーで中断"})
# 例外で止まった工程の状態（`rag_engine` の `_execution_step`）。
ANSWER_STEP_FAILED_STATUS = "エラーで中断"

type DisplayParams = dict[str, str | int | float | bool]


def answer_step_params(values: Mapping[str, object]) -> DisplayParams:
    """回答フローの工程の記録の値（工程名・LLM の回数・送信の回数・状態）を表示の値にする。

    LLM の回数は 1 回以上のときだけ、送信の回数は呼び出しより多いときだけ（#2966）、
    結果の説明は完了・中断以外のときだけ入れる。
    """
    params: DisplayParams = {}
    name = values.get("name")
    if isinstance(name, str) and name.strip():
        params["name"] = name.strip()
    llm_calls = values.get("llm_calls")
    if isinstance(llm_calls, int) and not isinstance(llm_calls, bool) and llm_calls > 0:
        params["llm_calls"] = llm_calls
        llm_sends = values.get("llm_sends")
        if isinstance(llm_sends, int) and not isinstance(llm_sends, bool) and llm_sends > llm_calls:
            params["llm_sends"] = llm_sends
    note = str(values.get("status") or "").strip()
    if note not in _PLAIN_STEP_STATUSES:
        params["note"] = note
    return params


def failure_details(
    kind: str | None,
    attributes: Mapping[str, object] | None = None,
    *,
    base: ChatProgressStepDetails | None = None,
) -> ChatProgressStepDetails | None:
    """失敗した工程の詳細（短い要約とエラーの種類・コード。#2342）。

    例外の本文は入れない（ORA のメッセージ・業務データを出さない）。工程の詳細（``base``）
    があれば、見出しと値はそのまま残し、行の先頭に要約を足す。
    """
    lines = [_STEP_FAILURE_SUMMARY.get(kind or "", _STEP_FAILURE_DEFAULT)]
    values = attributes or {}
    error_type = values.get("error_type")
    error_code = values.get("error_code")
    if isinstance(error_type, str) and error_type:
        lines.append(f"エラーの種類: {error_type}")
    if isinstance(error_code, str | int) and not isinstance(error_code, bool) and str(error_code):
        lines.append(f"エラーコード: {error_code}")
    if base is None:
        return chat_progress_details(lines=lines)
    return chat_progress_details(
        fields=[(field.label, field.value, bool(field.mono)) for field in base.fields],
        lines=[*lines, *base.lines],
        code=[(block.label, block.text, block.language) for block in base.code],
        truncated=bool(base.truncated),
    )


def answer_step_details(details: object, *, failed: bool = False) -> ChatProgressStepDetails | None:
    """回答フローの工程の詳細（`rag_engine` の構造化した詳細）。

    失敗した工程は要約の行を先頭に足す。
    """
    model = step_details_model(details)
    return failure_details(ANSWER_STEP_KIND, base=model) if failed else model
