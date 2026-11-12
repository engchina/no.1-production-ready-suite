"""質問の意味の推定を SQL の実行前の拒否条件にしない。"""

from typing import Any

import pytest

from app.features.nl2sql.models import AllowedObjects, Nl2SqlEngine, Nl2SqlProfile
from app.features.nl2sql.service import GeneratedSql, Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


@pytest.mark.parametrize(
    "condition", ["GROUP_ID='G1'", "ID='A1'", "OTHER_ID='A1'", "ID IN ('A1','A2')"]
)
def test_generation_keeps_requested_filters_without_classification_or_key_probe(
    monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    calls = []
    sql = "SELECT ID FROM APP.TABLE_A WHERE " + condition

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=sql,
            explanation="",
            engine_meta={},
        )

    def unexpected_probe(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("質問の列を推測する DB 照会は行わない")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service._oracle_adapter, "execute_select", unexpected_probe)
    result = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        "指定条件の全件を取得",
        Nl2SqlProfile(id="p", name="P"),
        AllowedObjects(),
        100,
        [],
    )
    assert result.generated_sql == sql
    assert len(calls) == 1
    assert calls[0]["generation_feedback"] is None
    assert "related_record_scopes" not in result.engine_meta
