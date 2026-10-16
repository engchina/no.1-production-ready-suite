"""LLM を呼ぶ回答生成の時間の上限と、時間切れの文言（#375）。

チャット・検索の回答と品質評価の 1 ケース（#383）は、質問の理解・根拠の評価と補正検索・
回答の生成と監査で LLM を何度か呼ぶ。検索だけの上限（旧 `rag_search_timeout_seconds`、
30 秒。#383 で削除）では足りないため、回答生成は `rag_answer_timeout_seconds`（既定 600 秒、
上限は LLM 1 回の timeout の設定の上限と同じ 600 秒。#1425）で打ち切る。時間切れのときは、進捗
（`SearchStageProgress`）から分かる最後の工程を文言に含める。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.rag.answer_engine import ANSWER_STEP_STAGE_PREFIX
from app.rag.answer_threads import answer_deadline
from app.rag.pipeline import SearchStageProgress, SearchStageProgressCallback

# 進捗の工程（SSE の `stage`）の利用者向けの名前。時間切れの文言（ERROR として保存する回答・
# SSE の error event・MCP の 504）に使う。画面の進捗の表示名は frontend の i18n
# （`search.stage.*`。`src/lib/answer-progress.ts`）と同じ名前にする。
ANSWER_STAGE_LABELS: dict[str, str] = {
    # 検索だけ(KB の検索テスト・レシピの検索比較。#593)
    "retrieval": "検索",
    "history_rewrite": "会話を踏まえた質問の書き換え",
    "field_filter": "検索条件の読み取り",
    "answer": "根拠の検索と回答の生成",
    # 回答側の安全チェック(#1146)
    "answer_guardrail": "回答の安全チェック",
}
# 回答フローの工程名（`rag_engine` の `_execution_step`）→ 利用者向けの名前（#2096）。画面の
# `frontend/src/lib/answer-process.ts` の `answerStepDisplayName`（i18n の `answer.step.*`）と
# 同じ名前にする（frontend の `answer-process.test.ts` が照合する）。工程名はログ・記録の識別に
# 使うので変えない。
ANSWER_STEP_DISPLAY_NAMES: dict[str, str] = {
    "質問の理解": "質問の条件の読み取り",
    "用語・ルールの確認": "用語・ルールの確認",
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
    "回答に使う画像の確認": "回答に使う画像の確認",
    "回答文の生成と根拠確認": "回答の作成と根拠の確認",
    "是正": "回答の手直し",
    "是正の打ち切り": "回答の手直しの打ち切り",
}
_STEP_ATTEMPT = re.compile(r"（(\d+)回目）$")
ANSWER_STAGE_BEFORE_START_LABEL = "検索の準備"
ANSWER_STAGE_UNKNOWN_LABEL = "処理"


def answer_stage_label(stage: str | None) -> str:
    """工程の利用者向けの名前。未開始は「検索の準備」、未知の工程は「処理」。"""
    if not stage:
        return ANSWER_STAGE_BEFORE_START_LABEL
    # 回答フローの各工程(#593)は、利用者向けの名前にする(#2096。知らない工程名はそのまま)。
    if stage.startswith(ANSWER_STEP_STAGE_PREFIX):
        return answer_step_display_name(stage.removeprefix(ANSWER_STEP_STAGE_PREFIX))
    return ANSWER_STAGE_LABELS.get(stage, ANSWER_STAGE_UNKNOWN_LABEL)


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


def format_timeout_limit(seconds: float) -> str:
    """上限の秒数の表記（例: 300 → 「5 分」、45 → 「45 秒」）。"""
    whole = max(1, round(seconds))
    if whole >= 60 and whole % 60 == 0:
        return f"{whole // 60} 分"
    return f"{whole} 秒"


def answer_timeout_message(stage: str | None, timeout_seconds: float | None) -> str:
    """回答生成の時間切れの文言。どの工程で時間切れになったかと、再試行の案内を含める。

    `timeout_seconds` が None のときは、通しの上限ではなく工程の中の時間切れ（LLM 1 回の
    timeout など）なので、上限の秒数を出さない。
    """
    limit = "時間内に"
    if timeout_seconds is not None:
        limit = f"上限の {format_timeout_limit(timeout_seconds)}以内に"
    return (
        f"回答の生成が{limit}終わりませんでした"
        f"（時間切れになった工程: {answer_stage_label(stage)}）。"
        "時間をおいて、もう一度送信してください。"
    )


class AnswerTimeoutError(TimeoutError):
    """回答生成の時間切れ。最後の工程と利用者向けの文言を持つ。"""

    def __init__(self, stage: str | None, timeout_seconds: float | None) -> None:
        self.stage = stage
        self.timeout_seconds = timeout_seconds
        self.user_message = answer_timeout_message(stage, timeout_seconds)
        super().__init__(self.user_message)

    @property
    def original_error(self) -> Exception:
        """監査に残す元の例外（通しの上限なら asyncio の TimeoutError）。"""
        cause = self.__cause__
        return cause if isinstance(cause, Exception) else self


class StageTracker:
    """進捗の callback を包み、今の工程（最後に始まった、まだ終わっていない工程）を覚える。

    工程は入れ子になる（例: 追加の検索の中の根拠の整理）。終わった工程を外し、残っている
    最後の工程を「今の工程」とする。どれも実行中でなければ、最後に通知された工程を返す。
    """

    def __init__(self, inner: SearchStageProgressCallback | None = None) -> None:
        self._inner = inner
        self._active: list[str] = []
        self._last: str | None = None

    @property
    def current_stage(self) -> str | None:
        return self._active[-1] if self._active else self._last

    async def __call__(self, progress: SearchStageProgress) -> None:
        self._last = progress.stage
        if progress.outcome == "started":
            self._active.append(progress.stage)
        elif progress.stage in self._active:
            # 同じ名前の工程は後に始まったものから閉じる。
            index = len(self._active) - 1 - self._active[::-1].index(progress.stage)
            del self._active[index]
        if self._inner is not None:
            await self._inner(progress)


def answer_timeout_seconds(settings: Settings) -> float:
    """LLM を呼ぶ回答生成（チャット・検索の回答）の通しの上限（秒）。"""
    return float(settings.rag_answer_timeout_seconds)


async def run_answer_with_timeout[T](
    operation: Callable[[StageTracker], Awaitable[T]],
    settings: Settings,
    progress_callback: SearchStageProgressCallback | None = None,
    *,
    timeout_seconds: float | None = None,
) -> T:
    """回答生成を `rag_answer_timeout_seconds` で打ち切る。

    `timeout_seconds` を渡すと、その秒数で打ち切る（品質評価で、評価全体の残り時間が回答生成の
    上限より短いとき。#383）。

    打ち切り・取り消しは回答の thread にも伝える（期限を文脈に置き、抜けるときに取り消す。#1422）。
    thread の中の LLM の呼び出しは期限を過ぎたら止まり、再試行しない。

    `operation` には工程を記録する callback（`StageTracker`）を渡す。時間切れのときは
    最後の工程を持つ `AnswerTimeoutError` を送出する（`TimeoutError` の派生）。工程の中で
    起きた `TimeoutError`（LLM 1 回の timeout など）も、工程が分かるように同じ例外にする。
    """
    tracker = StageTracker(progress_callback)
    timeout = answer_timeout_seconds(settings) if timeout_seconds is None else timeout_seconds
    budget = asyncio.timeout(timeout)
    try:
        # 回答の thread にも同じ期限を渡し、打ち切り・取り消しのときに thread の LLM の呼び出しを
        # 止める（#1422。`app/rag/answer_threads.py`）。
        async with budget:
            with answer_deadline(timeout):
                return await operation(tracker)
    except AnswerTimeoutError:
        raise
    except TimeoutError as exc:
        raise AnswerTimeoutError(
            tracker.current_stage, timeout if budget.expired() else None
        ) from exc
