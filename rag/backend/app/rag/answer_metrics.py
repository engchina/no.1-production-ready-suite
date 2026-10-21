"""標準回答による評価の指標と合否(評価の基準の指標と閾値。#680)。

評価の基準の 9 指標のうち、1 件の回答と標準回答で測れる 5 つを、評価の基準で選んだ suite
(standard / strict)の閾値で判定する。すべて閾値以上なら合格(品質評価の answer_pass_rate が
数える合否)。正解の文書が要る context_recall / mrr と、期待する語が要る answer_keyword_hit_rate
は測らない。

- faithfulness / citation_traceability_coverage: 品質評価と同じ決定的な計算(回答と引用)。
  faithfulness は語句の一致による近似で、1 件の回答では言い換えだけで閾値を下回る。同じ観点は
  LLM の主張の監査(claim_support_rate)が判定するので、1 件の回答では参考値にして合否に使わない
- claim_support_rate: LLM の主張の監査。根拠のない主張・矛盾する主張が無ければ 1
- requirement_coverage: LLM の照合。標準回答の required 項目の対応率
- refusal_accuracy: 標準回答がある質問は答えるべき質問。拒答なら 0
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.grounded import (
    generated_section_lines,
    is_structural_line,
)

from app.config import Settings
from app.rag.evaluation_adapter import resolve_evaluation_adapter
from app.rag.file_processing_evaluation import citation_traceability_coverage
from app.rag.grounding_metrics import evaluate_groundedness
from app.schemas.search import RetrievedChunk

UNSUPPORTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
# 1 件の回答では表示だけにして、合否に使わない指標。
REFERENCE_ONLY_METRICS = frozenset({"faithfulness"})


# #2738 より前に Agent の最終の検証が回答の末尾に足していた「資料で確認できず除いた内容」の
# 節の見出し（#1456）。保存済みの回答の faithfulness のために持ち続ける。今の Agent の節の見出しは
# platform/contracts/answer-passages/unverified-section.json の headings（RAG の回答の節と
# 同じ見出し）。
AGENT_UNVERIFIED_HEADING = "資料で確認できず除いた内容"


def agent_system_headings() -> frozenset[str]:
    """Agent の最終の検証が回答の末尾に足す節の見出し（#1456・#2738）。

    以前の「資料で確認できず除いた内容」と、根拠と矛盾して外した内容・確認が必要な点の節（RAG の
    回答と同じ見出し。言語資源の answer_labels）。どれも外した段落・本文の印の段落の引用と理由で、
    本文の主張ではない。
    """
    labels = answer_labels()
    return frozenset({AGENT_UNVERIFIED_HEADING, labels.removed_section, labels.marked_section})


# 見出しの行（Markdown の見出し・太字だけの行。後ろの「：」は許す）。
_HEADING_LINE = re.compile(r"(?:#{1,6}\s+(?P<md>.+?)|\*\*(?P<bold>[^*\n]+)\*\*)\s*[:：]?")


def _heading_title(line: str) -> str | None:
    match = _HEADING_LINE.fullmatch(line.strip())
    if match is None:
        return None
    return (match.group("md") or match.group("bold") or "").strip().strip("*").strip()


def without_unverified_section(answer: str) -> str:
    """Agent の最終の検証が足した節（``agent_system_headings``。次の見出しの前まで）を除く。

    節は回答の本文ではなく、検証が外した段落・印の段落の原文と理由なので、忠実さ（語句の一致）に
    数えない（#1456。外した段落が多い Run ほど faithfulness が下がり、A と D の比較を歪めていた）。
    印の段落そのものは本文に残るので数える（#2738）。
    """
    headings = agent_system_headings()
    kept: list[str] = []
    inside = False
    for line in answer.splitlines():
        title = _heading_title(line)
        if title is not None:
            inside = title in headings
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def grounding_text(answer: str) -> str:
    """忠実さの照合に使う回答の本文。

    回答生成が付けた見出し・出典行・定型文（「参考情報」の節の直下の説明を含む。#2040）と、
    「根拠と矛盾し除いた内容」「確認が必要な点」の節（#2738）と、Agent の最終の検証が足した節
    （#1456）は主張ではないので外す。印の付いた段落は本文の主張として数える（#2738）。
    """
    body = without_unverified_section(answer)
    # 読めなかった章節・時間の上限で省いた処理の行もシステムの案内なので外す（#2040）。
    generated = generated_section_lines(body)
    return "\n".join(
        line
        for line in body.splitlines()
        if line.strip()
        and not is_structural_line(line)
        and line.strip() != answer_labels().neutral_summary
        and line.strip() not in generated
    )


def is_abstained_answer(
    answer: str, citations: Sequence[RetrievedChunk], insufficient_reason: str
) -> bool:
    """回答が「資料から答えられない」旨だけか(拒答)。

    本文か引用が無い回答は拒答。不足の理由があり、モデルが使った根拠が 1 つも無い回答も拒答と
    する(必要な根拠を原文のまま示すだけの回答を含む)。
    """
    if not answer.strip() or not citations:
        return True
    if not insufficient_reason.strip():
        return False
    # 親（まとまり）だけを参照した根拠（#2710）も、回答が資料を使った印にする。
    return not any(
        chunk.metadata.get("evidence_model_used") or chunk.metadata.get("evidence_parent_used")
        for chunk in citations
    )


def claims_supported(claims: Sequence[Mapping[str, Any]]) -> bool:
    """根拠のない主張・根拠と矛盾する主張が無いか。"""
    return not any(str(claim.get("status")) in UNSUPPORTED_CLAIM_STATUSES for claim in claims)


def score_answer_evaluation(
    evaluation: Mapping[str, Any],
    *,
    answer: str,
    citations: Sequence[RetrievedChunk],
    insufficient_reason: str,
    settings: Settings,
) -> dict[str, Any]:
    """LLM の判定(照合・監査)に、評価の基準の指標・閾値・合否を足す。未完了の評価はそのまま。"""
    if evaluation.get("status") != "completed":
        return dict(evaluation)
    params = resolve_evaluation_adapter(settings)
    abstained = is_abstained_answer(answer, citations, insufficient_reason)
    claims = [claim for claim in evaluation.get("claim_checks") or [] if isinstance(claim, Mapping)]
    coverage = evaluation.get("requirement_coverage")
    values: dict[str, float | None] = {
        "faithfulness": (
            None
            if abstained
            else evaluate_groundedness(
                grounding_text(answer), "\n".join(chunk.text for chunk in citations)
            ).score
        ),
        "citation_traceability_coverage": (
            citation_traceability_coverage(citations) if citations else None
        ),
        "claim_support_rate": 1.0 if claims_supported(claims) else 0.0,
        "requirement_coverage": float(coverage) if isinstance(coverage, int | float) else None,
        "refusal_accuracy": 0.0 if abstained else 1.0,
    }
    metrics = []
    for name, value in values.items():
        threshold = getattr(params.thresholds, name)
        if value is None or threshold is None:
            continue
        metrics.append(
            {
                "name": name,
                "value": round(value, 4),
                "threshold": threshold,
                "passed": value >= threshold,
                "reference": name in REFERENCE_ONLY_METRICS,
            }
        )
    judged = [metric for metric in metrics if not metric["reference"]]
    return {
        **evaluation,
        "suite": params.suite,
        "metrics": metrics,
        "passed": bool(judged) and all(metric["passed"] for metric in judged),
    }
