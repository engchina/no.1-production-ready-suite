"""業務プロファイル推薦の信頼度公式（相対信頼度）の回帰テスト。

旧実装は `confidence = score / 6` を分類器経路・決定論経路で共有し、
- 学習済みモデルの predict_proba(0..1) が proba/6(≈0.167) に潰れる
- 実マッチ 0 でも +0.5/+0.2 の見かけ倒し加点で非ゼロ信頼度が出る
という欠陥があった。ここでは新しい `_relative_confidence` と経路別の信頼度を固定する。
"""

from __future__ import annotations

from typing import Any, cast

from app.features.nl2sql.models import (
    ClassifierPredictRequest,
    ClassifierTrainRequest,
    Nl2SqlProfile,
    ProfileRecommendationRequest,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


class _FakeEmbeddingClient:
    """入金/支払を含む文だけ別次元に立てる決定論 embedding。"""

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * 1536
            vector[1 if ("入金" in text or "支払" in text) else 2] = 1.0
            vectors.append(vector)
        return vectors


def test_relative_confidence_math() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    rc = service._relative_confidence
    # 実マッチ皆無 → 0.0（「8%」を撲滅）
    assert rc([]) == 0.0
    assert rc([0.0, 0.0]) == 0.0
    # 独走: dominance=1.0, strength=3/(3+3)=0.5 → 0.5
    assert rc([3.0, 0.0]) == 0.5
    # 拮抗（曖昧）: dominance=0.5, strength=2/(2+3)=0.4 → 0.2（0.3 ゲート未満）
    assert rc([2.0, 2.0]) == 0.2
    # 強い独走はさらに高い
    assert rc([6.0, 0.0]) > 0.5


def test_deterministic_recommend_zero_confidence_for_irrelevant_question() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="hr",
            name="人事プロファイル",
            allowed_tables=["EMPLOYEE"],
            glossary={"社員": "EMPLOYEE.FULL_NAME"},
        )
    )
    recommendation = service.recommend_profile(
        ProfileRecommendationRequest(question="今日の天気はどうですか")
    )
    # 何にも一致しない → 信頼度 0、候補スコアも全て 0（見かけ倒し加点が混入しない）
    assert recommendation.confidence == 0.0
    assert all(0.0 <= c.score <= 1.0 for c in recommendation.candidates)
    assert all(c.score == 0.0 for c in recommendation.candidates)


def test_deterministic_recommend_confident_for_matching_question() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="hr",
            name="人事プロファイル",
            allowed_tables=["EMPLOYEE"],
            glossary={"社員": "EMPLOYEE.FULL_NAME"},
        )
    )
    recommendation = service.recommend_profile(
        ProfileRecommendationRequest(question="社員の一覧を見たい")
    )
    assert recommendation.recommended_profile_id == "hr"
    # 実マッチがある独走ケース → ヒント表示閾値(0.3)以上、上限 1.0 以内
    assert 0.3 <= recommendation.confidence <= 1.0
    # 候補スコアは 0..1 の相対シェア（「スコア X%」が 0-100% に収まる）
    assert all(0.0 <= c.score <= 1.0 for c in recommendation.candidates)
    assert recommendation.recommendation_source == "deterministic"


def test_classifier_confidence_uses_probability_not_divided_by_six() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._embedding_client = _FakeEmbeddingClient()
    service.create_profile(
        Nl2SqlProfile(
            id="payment",
            name="入金管理",
            allowed_tables=["PAYMENTS", "INVOICES"],
            glossary={"入金": "PAYMENTS.PAID_AT"},
        )
    )
    payload = "\n".join(
        [
            "CATEGORY,TEXT",
            "標準業務プロファイル,請求金額が大きい取引先を見たい",
            "標準業務プロファイル,売上合計を顧客別に確認したい",
            "入金管理,入金が遅れている請求を確認したい",
            "入金管理,未入金の支払状況を見たい",
        ]
    ).encode()
    service.import_classifier_training_data(
        filename="training_data.csv", content=payload, replace=True
    )
    status = service.train_classifier(ClassifierTrainRequest())
    assert status.ready

    prediction = service.predict_classifier(
        ClassifierPredictRequest(question="未入金の請求を確認したい")
    )
    recommendation = service.recommend_profile(
        ProfileRecommendationRequest(question="未入金の請求を確認したい")
    )
    assert recommendation.recommendation_source == "classifier"
    best_proba = prediction.candidates[0].score
    # 信頼度は predict_proba そのもの（旧 proba/6 の潰れが無い）
    assert recommendation.confidence == round(best_proba, 3)
    assert recommendation.confidence > 0.2  # proba/6 なら最大 ≈0.167 に潰れていた
