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

工程の利用者向けの名前（名詞。RAG の画面と同じ文言）は、共通の span の表の書き手が
`span_display_name` で span の名前の値 ``display_name`` に書く（#3272）。別の製品の画面
（Agent の実行の詳細）は RAG の工程の定義を持たず、この名前をそのまま出す。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pr_backend_core.chat_progress import ChatProgressStepDetails, chat_progress_details
from pr_backend_core.observability.tracing import SpanUpdate

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

# 進捗の工程（SSE の `stage`。span の属性 `pr.stage`）の利用者向けの名前。時間切れの文言（ERROR
# として保存する回答・SSE の error event・MCP の 504）と、span に書く工程の名前（#3272）に使う。
# 画面の進捗の表示名は frontend の i18n（`search.stage.*`。`src/lib/answer-progress.ts`）と同じ
# 名前にする（frontend の `answer-process.test.ts` が照合する）。
ANSWER_STAGE_LABELS: dict[str, str] = {
    # 検索だけ(KB の検索テスト・レシピの検索比較。#593)
    "retrieval": "検索",
    "history_rewrite": "会話を踏まえた質問の書き換え",
    # 会話の添付の根拠の候補の選択(添付の読み込みと rerank。#3475)
    "attachment_evidence": "会話の添付からの根拠の選択",
    "field_filter": "検索条件の抽出（Metadata Filter Extraction）",
    "answer": "質問の理解から回答の作成まで",
    # 回答側の安全チェック(#1146)
    "answer_guardrail": "回答の安全チェック",
    # 回答の記録・会話の回答の保存(#2342)
    "answer_record_save": "回答の記録の保存",
    "chat_message_save": "会話への回答の保存",
}
# 回答フローの工程名（`rag_engine` の `_execution_step`）→ 利用者向けの名前（#2096）。画面の
# `frontend/src/lib/answer-process.ts` の `answerStepDisplayName`（i18n の `answer.step.*`）と
# 同じ名前にする（frontend の `answer-process.test.ts` が照合する）。工程名はログ・記録の識別に
# 使うので変えない。
ANSWER_STEP_DISPLAY_NAMES: dict[str, str] = {
    "質問の理解": "質問の理解（Query Understanding）",
    "用語・同義語の確認": "用語・同義語の確認",
    "回答ルールの確認": "回答ルールの確認",
    "検索の準備確認": "検索の準備の確認",
    "質問拡張戦略": "質問の言い換え",
    "検索文と検索語の確定": "検索文と検索語の決定",
    "回答生成フロー": "根拠の検索と回答の作成",
    "検索語の再確定（書き換え後）": "検索語の決め直し",
    "文書検索": "文書の検索",
    "Rerank": "検索結果の並べ替え",
    "文書の選択": "文書の絞り込み",
    "文書の選択の解除": "文書の絞り込みの解除",
    "根拠確認": "根拠の確認",
    "根拠確認の結論": "根拠の確認の結論",
    "補正検索の準備": "探し直しの準備",
    "文脈の追加": "前後の文脈の追加",
    "画面の選択": "関係する画面の選択",
    "章節の拡張": "関係する章節の追加",
    "要求ごとの根拠の確認": "要求ごとの根拠の確認",
    "回答に添付する画像": "回答に添付する画像",
    "回答文の生成と根拠確認": "回答の作成と根拠の確認",
    "是正": "回答の手直し",
    "是正の打ち切り": "回答の手直しの打ち切り",
}
_STEP_ATTEMPT = re.compile(r"（(\d+)回目）$")
ANSWER_STAGE_BEFORE_START_LABEL = "検索の準備"
ANSWER_STAGE_UNKNOWN_LABEL = "処理"


def answer_step_display_name(name: str) -> str:
    """回答フローの工程名の利用者向けの名前（回数つきは「文書の検索（2 回目）」の形）。"""
    value = name.strip()
    if not value:
        return ANSWER_STAGE_UNKNOWN_LABEL
    if value in ANSWER_STEP_DISPLAY_NAMES:
        return ANSWER_STEP_DISPLAY_NAMES[value]
    attempt = _STEP_ATTEMPT.search(value)
    base = value[: attempt.start()] if attempt else value
    if attempt is None or base not in ANSWER_STEP_DISPLAY_NAMES:
        return value
    return f"{ANSWER_STEP_DISPLAY_NAMES[base]}（{attempt.group(1)} 回目）"


def span_display_name(update: SpanUpdate) -> str | None:
    """RAG の span の利用者向けの名前（名詞。RAG の画面の工程の名前と同じ。#3272）。

    回答フローの工程（種類 ``answer_step``）は工程名から、回答フローの外の工程と全体を包む工程は
    段（``pr.stage``）から決める。技術の span・知らない段は None（画面が共通の規則で名前を付ける）。
    """
    display = update.display
    if display is not None and display.kind == ANSWER_STEP_KIND:
        name = (display.params or {}).get("name")
        return answer_step_display_name(name) if isinstance(name, str) and name.strip() else None
    stage = update.attributes.get("pr.stage")
    return ANSWER_STAGE_LABELS.get(stage) if isinstance(stage, str) else None


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
