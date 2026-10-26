"""判定の校正: LLM による判定を人手の判定（正解のラベル）と照らす（#3418・#2577）。

RAG の検索の評価器の校正（`rag_engine.evaluation.crag_grader_eval`。検索の候補の関連・根拠の足りる
かのラベル）とは対象が違うので分ける。ここは回答の品質の観点（rubric の観点）ごとのラベルを使い、
一致の集計は platform の `pr_evaluation_scoring.agreement` を共有する。

人手の判定の形（`LabelSet`。JSON）:

    {"rubric_id": "answer_quality",
     "items": [{"id": "c01", "split": "holdout",
                "question": "…", "answer": "…", "standard_answer": "…",
                "evidence": [{"id": "e1", "text": "…"}],
                "labels": {"requirement_coverage": "pass", "answer_correctness": "fail",
                           "claim_support": "pass"},
                "labeler": "reviewer-1", "note": "…"}]}

- `split`: dev は rubric の調整に使ってよい項目、holdout は調整に使わず合否の判定だけに使う項目。
- `labels` は観点ごとの pass / fail（その観点を人が判定できない項目は書かない）。

校正の合否（`CalibrationThresholds`）は holdout の項目で、観点ごとに件数・判定率・Cohen の κ・誤
合格の率を閾値と比べる。合格した観点だけ、同じ rubric（sha256）・同じモデルの判定を合否に使える。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pr_evaluation_scoring import LabelPair, agreement
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pr_suite_evaluation.dataset import canonical_sha256
from pr_suite_evaluation.judge.client import JudgeClient
from pr_suite_evaluation.judge.judging import (
    JudgeInput,
    Judgement,
    JudgeSettings,
    judge_item,
    prompt_sha256,
)
from pr_suite_evaluation.judge.rubric import FAIL, PASS, Rubric

CALIBRATION_SCHEMA_VERSION = 1
HUMAN_LABELS = (PASS, FAIL)
SPLITS = ("dev", "holdout")


class LabeledEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    text: str


class LabeledItem(BaseModel):
    """人手の判定の 1 件。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    split: Literal["dev", "holdout"]
    question: str = Field(..., min_length=1)
    answer: str
    standard_answer: str | None = None
    evidence: list[LabeledEvidence] = Field(default_factory=list)
    labels: dict[str, Literal["pass", "fail"]] = Field(..., min_length=1)
    labeler: str | None = None
    note: str | None = None

    def judge_input(self) -> JudgeInput:
        return JudgeInput(
            item_id=self.id,
            question=self.question,
            answer=self.answer,
            standard_answer=self.standard_answer,
            evidence=tuple((item.id, item.text) for item in self.evidence),
        )


class LabelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    description: str | None = None
    items: list[LabeledItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [item.id for item in self.items]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"項目の id が重複しています: {', '.join(duplicates)}")
        return self

    def check_rubric(self, rubric: Rubric) -> None:
        if self.rubric_id != rubric.id:
            raise ValueError(
                f"人手の判定の rubric（{self.rubric_id}）と rubric（{rubric.id}）が違います。"
            )
        unknown = sorted(
            {key for item in self.items for key in item.labels} - set(rubric.aspect_ids)
        )
        if unknown:
            raise ValueError(f"rubric に無い観点のラベルです: {', '.join(unknown)}")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_labels(path: Path) -> LabelSet:
    return LabelSet.model_validate(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class CalibrationThresholds:
    """校正の合否の閾値（holdout の観点ごと）。"""

    min_items: int = 50
    min_kappa: float = 0.6
    max_false_pass_rate: float = 0.1
    min_coverage: float = 0.95

    def provenance(self) -> dict[str, Any]:
        return {
            "split": "holdout",
            "min_items": self.min_items,
            "min_kappa": self.min_kappa,
            "max_false_pass_rate": self.max_false_pass_rate,
            "min_coverage": self.min_coverage,
        }


class AgreementSummary(BaseModel):
    total: int
    judged: int
    unjudged: int
    coverage: float | None
    accuracy: float | None
    kappa: float | None
    false_pass_rate: float | None
    false_fail_rate: float | None
    confusion: dict[str, dict[str, int]]
    # 人手と判定が違った項目（ラベルを見直すときに読む）。
    disagreements: list[str] = Field(default_factory=list)


class AspectCalibration(BaseModel):
    splits: dict[str, AgreementSummary] = Field(default_factory=dict)
    passed: bool
    failures: list[str] = Field(default_factory=list)


class CalibrationReport(BaseModel):
    """校正の結果（判定の結果も持ち、人手の判定を直したら `--judgements` で集計だけやり直せる）。"""

    schema_version: int = CALIBRATION_SCHEMA_VERSION
    created_at: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    judgements: list[Judgement] = Field(default_factory=list)
    aspects: dict[str, AspectCalibration] = Field(default_factory=dict)
    passed: bool


def _summary(pairs: Sequence[LabelPair]) -> AgreementSummary:
    result = agreement(pairs, HUMAN_LABELS, positive=PASS, negative=FAIL)
    return AgreementSummary(
        total=result.total,
        judged=result.judged,
        unjudged=result.unjudged,
        coverage=result.coverage,
        accuracy=result.accuracy,
        kappa=result.kappa,
        false_pass_rate=result.false_pass_rate,
        false_fail_rate=result.false_fail_rate,
        confusion=result.confusion,
        disagreements=[item.item_id for item in pairs if item.judge != item.human],
    )


def _gate(summary: AgreementSummary | None, thresholds: CalibrationThresholds) -> list[str]:
    if summary is None:
        return ["holdout の項目がありません"]
    failures: list[str] = []
    if summary.total < thresholds.min_items:
        failures.append(f"holdout の件数 {summary.total} < {thresholds.min_items}")
    if summary.coverage is None or summary.coverage < thresholds.min_coverage:
        failures.append(f"判定率 {summary.coverage} < {thresholds.min_coverage}")
    if summary.kappa is None or summary.kappa < thresholds.min_kappa:
        failures.append(f"κ {summary.kappa} < {thresholds.min_kappa}")
    if summary.false_pass_rate is None or summary.false_pass_rate > thresholds.max_false_pass_rate:
        failures.append(f"誤合格の率 {summary.false_pass_rate} > {thresholds.max_false_pass_rate}")
    return failures


def calibrate(
    labels: LabelSet,
    rubric: Rubric,
    *,
    judgements: Sequence[Judgement],
    judge_provenance: Mapping[str, Any],
    settings: JudgeSettings,
    thresholds: CalibrationThresholds,
) -> CalibrationReport:
    """判定の結果と人手の判定の一致を観点ごと・区分ごとに集計し、holdout で合否を決める。"""
    labels.check_rubric(rubric)
    by_id = {item.item_id: item for item in judgements}
    missing = [item.id for item in labels.items if item.id not in by_id]
    if missing:
        raise ValueError(f"判定の無い項目があります: {', '.join(missing[:10])}")
    aspects: dict[str, AspectCalibration] = {}
    for aspect_id in rubric.aspect_ids:
        splits: dict[str, AgreementSummary] = {}
        for split in SPLITS:
            pairs: list[LabelPair] = []
            for item in labels.items:
                human = item.labels.get(aspect_id)
                if item.split != split or human is None:
                    continue
                result = by_id[item.id].aspects.get(aspect_id)
                judge = result.label if result is not None and result.status == "scored" else None
                pairs.append(LabelPair(item_id=item.id, human=human, judge=judge))
            if pairs:
                splits[split] = _summary(pairs)
        failures = _gate(splits.get("holdout"), thresholds)
        aspects[aspect_id] = AspectCalibration(
            splits=splits, passed=not failures, failures=failures
        )
    return CalibrationReport(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provenance={
            "rubric": rubric.provenance(),
            "prompt_sha256": prompt_sha256(rubric),
            "labels": {"sha256": labels.sha256, "item_count": len(labels.items)},
            "judge": dict(judge_provenance),
            "settings": settings.provenance(),
            "thresholds": thresholds.provenance(),
        },
        judgements=list(judgements),
        aspects=aspects,
        passed=all(item.passed for item in aspects.values()),
    )


def judge_labels(
    labels: LabelSet,
    rubric: Rubric,
    client: JudgeClient,
    settings: JudgeSettings,
) -> list[Judgement]:
    """人手の判定の項目を判定する（モデルを呼ぶ）。"""
    labels.check_rubric(rubric)
    return [judge_item(item.judge_input(), rubric, client, settings) for item in labels.items]
