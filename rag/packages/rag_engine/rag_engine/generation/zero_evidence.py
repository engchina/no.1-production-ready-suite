"""根拠 0 件で回答を作らずに終えたとき、どの段で 0 件になったかを決める（#2401）。

回答の記録の「根拠 0 件・生成 0 回」だけでは、検索の候補が無かったのか、根拠の組み立てで 0 件になったのかが
分からない。段と件数を工程の詳細と回答の記録（``generation_trace.zero_evidence``）に残し、検索をやり直さずに
原因の段を追えるようにする。
"""

from __future__ import annotations

from typing import Any

from rag_engine.generation.answer_models import AnswerContext

# 段の名前と表示の文。``filter`` は呼び出し側（検索の条件を持つ backend）が、自動で足した検索条件が
# 効いたまま候補が 0 件だったときに ``retrieval`` を置き換える。
ZERO_EVIDENCE_STAGE_LABELS = {
    "retrieval": "文書検索（検索の候補が 0 件）",
    "filter": "文書検索（自動で足した検索条件で候補が 0 件）",
    "context": "根拠の組み立て（候補はあるが回答に渡す根拠が 0 件）",
}


def zero_evidence_stage(context: AnswerContext) -> dict[str, Any]:
    """根拠 0 件になった段と、その判断に使った件数を返す。

    - 検索の候補が 1 件も無い: ``retrieval``
    - 候補はあるが、根拠の組み立て（文書の選択・文字数の上限など）で 0 件になった: ``context``
    """
    candidate_count = max(len(context.ranked_candidates), len(context.expansion_records))
    stage = "context" if candidate_count else "retrieval"
    return {"stage": stage, "label": ZERO_EVIDENCE_STAGE_LABELS[stage], "candidate_count": candidate_count}
