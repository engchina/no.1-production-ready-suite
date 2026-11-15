"""回答の保存・履歴管理を共有する。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_engine.ports import ArtifactStore
from rag_engine.resources.filesystem import FileArtifactStore


def _create_answer_id(run_id: str, question: str) -> str:
    seed = f"{run_id}\0{time.time_ns()}\0{question}".encode()
    return hashlib.sha256(seed).hexdigest()[:16]


def write_answer_payload(output_dir: Path, run_id: str, payload: dict[str, Any], *, store: ArtifactStore | None = None) -> str:
    """明示された保存先に回答 JSON を原子的に保存する。既存形式を維持する。"""
    answer_id = str(payload.get("answer_id") or "").strip()
    if not answer_id or not run_id or any(part in {".", ".."} or "/" in part or "\\" in part for part in (run_id, answer_id)):
        raise ValueError("run_id and answer_id must be nonempty path components")
    target = store if store is not None else FileArtifactStore(Path(output_dir))
    target.write(
        f"{run_id}/answers/{answer_id}.json",
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return answer_id


@dataclass(frozen=True)
class SavedAnswer:
    """保存された回答の識別子と既存 viewer 互換データ。"""

    run_id: str
    answer_id: str
    payload: dict[str, Any]


def save_answer_result(
    result,
    settings,
    *,
    run_id: str = "",
    question: str = "",
    standard_answer: str = "",
    store: ArtifactStore | None = None,
    evaluation_provider: str | None = None,
    evaluation_enabled: bool = True,
) -> SavedAnswer | None:
    """標準回答があれば指定 provider（未指定時は既定 LLM）で評価し、回答と履歴を保存する。

    evaluation_enabled=False は LLM 評価を省略し、標準回答・生成時の確認状態を保持する。
    既存の直接呼び出しとの互換性のため既定値は True。UI は明示的に指定する。
    評価失敗でも回答は保存する。出典 run のない不足結果は保存しない。
    """
    from rag_engine.generation.answering import answer_result_payload, format_answer_response

    source_id = str(run_id or result.primary_source_run_id or "").strip()
    if not source_id:
        return None
    answer_id = _create_answer_id(source_id, result.original_question)
    payload = answer_result_payload(
        result, answer_id=answer_id, run_id=source_id, question=question or result.original_question, standard_answer=standard_answer
    )
    if evaluation_enabled:
        from rag_engine.evaluation.answer_eval import evaluate_answer_payload

        payload["evaluation"] = evaluate_answer_payload(payload, settings, provider_id=evaluation_provider)
    else:
        payload["evaluation"] = {
            "status": "not_evaluated",
            "message": "LLM による回答評価がオフのため、評価していません。",
        }
    evaluation = payload["evaluation"]
    if evaluation["status"] == "completed":
        # 外部確認を UI と feedback が同じ値で参照する。
        payload["external_data_required"] = evaluation["external_data_required"]
        payload["external_data_items"] = list(evaluation["external_data_items"])
        external_label = "外部データ確認が必要"
        payload["question_type"] = [value for value in payload["question_type"] if value != external_label]
        if evaluation["external_data_required"]:
            payload["needs_human_review"] = True
            payload["question_type"].append(external_label)
        # 外部確認が不要でも、根拠不足・専門判断などによる既存の人手確認は解除しない。
        # 旧クライアントが読む整形済み answer にも古い人工確認ラベルを残さない。
        payload["answer"] = format_answer_response(
            json.dumps(
                {
                    "answer": payload["answer_text"],
                    **{
                        key: payload[key]
                        for key in (
                            "confidence",
                            "question_type",
                            "cited_evidence",
                            "reasoning_summary",
                            "insufficient_reason",
                            "needs_human_review",
                            "external_data_required",
                            "external_data_items",
                        )
                    },
                },
                ensure_ascii=False,
            )
        )
    write_answer_payload(settings.output_dir, source_id, payload, store=store)
    return SavedAnswer(source_id, answer_id, payload)
