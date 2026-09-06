"""明示選択したエンジンの失敗境界に関する回帰テスト。

本番(oracle runtime)では、Select AI / Enterprise AI の失敗を質問を無視したテンプレート
SQL で埋めず、別エンジンへ暗黙 fallback しない。
local/CI の deterministic runtime は従来どおりテンプレート SQL でデモできる。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlEngine,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.oracle_adapter import OracleAdapterError
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore

_DIRECT_SQL = (
    '{"sql":"SELECT EMPLOYEE_ID FROM APP.EMPLOYEE","explanation":"社員 ID を取得します。"}'
)


def _catalog() -> SchemaCatalog:
    # deterministic runtime のテンプレート SQL / mock 実行は 4 列以上の表を前提にする。
    return SchemaCatalog(
        refreshed_at="2026-09-02T00:00:00+00:00",
        schema_fingerprint="engine-fallback-v1",
        current_owner="APP",
        tables=[
            SchemaTable(
                owner="APP",
                table_name="EMPLOYEE",
                logical_name="社員",
                columns=[
                    SchemaColumn(
                        column_name="EMPLOYEE_ID",
                        logical_name="社員 ID",
                        data_type="NUMBER",
                        nullable=False,
                    ),
                    SchemaColumn(
                        column_name="EMPLOYEE_NAME", logical_name="社員名", data_type="VARCHAR2"
                    ),
                    SchemaColumn(column_name="SALARY", logical_name="給与", data_type="NUMBER"),
                    SchemaColumn(column_name="HIRED_AT", logical_name="入社日", data_type="DATE"),
                ],
            )
        ],
    )


class _FakeEnterpriseAiClient:
    def __init__(self, text: str, *, fail: bool = False, configured: bool = True) -> None:
        self.text = text
        self.fail = fail
        self.configured = configured
        self.calls = 0

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "enterprise-nl2sql-model"

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> str:
        del prompt, context, system_prompt, timeout_seconds, max_output_tokens, max_retries
        self.calls += 1
        if self.fail:
            raise EnterpriseAiDirectError("Enterprise AI から応答がありません。")
        return self.text


def _oracle_runtime_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    direct: _FakeEnterpriseAiClient,
) -> Nl2SqlService:
    """Oracle 呼び出しが必ず失敗する oracle runtime 相当の service。"""

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _catalog()  # noqa: SLF001 - white-box contract test
    service._enterprise_ai_client = direct  # noqa: SLF001
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_assert_select_ai_scope_ready", lambda _profile: None)

    def _select_ai_down(**_kwargs: Any) -> str:
        raise OracleAdapterError("Select AI: ORA-20000 profile unavailable")

    def _agent_down(**_kwargs: Any) -> tuple[str, str]:
        raise OracleAdapterError("Select AI Agent: team execution failed")

    monkeypatch.setattr(
        service._oracle_adapter, "generate_select_ai_sql", _select_ai_down
    )  # noqa: SLF001
    monkeypatch.setattr(
        service._oracle_adapter, "run_select_ai_agent_team", _agent_down
    )  # noqa: SLF001
    return service


def _generate(service: Nl2SqlService, engine: Nl2SqlEngine) -> Any:
    return service._generate_selected_engine(  # noqa: SLF001
        question="社員一覧を確認したい",
        engine=engine,
        profile=service.get_profile(None),
        allowed=AllowedObjects(),
        row_limit=10,
    )


def test_explicit_select_ai_failure_is_an_error_in_oracle_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _oracle_runtime_service(monkeypatch, direct=_FakeEnterpriseAiClient(_DIRECT_SQL))

    # 旧実装はテンプレート SQL を engine=select_ai の成功結果として返していた。
    with pytest.raises(RuntimeError, match="ORA-20000"):
        _generate(service, Nl2SqlEngine.SELECT_AI)


def test_explicit_enterprise_ai_failure_is_an_error_in_oracle_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _oracle_runtime_service(
        monkeypatch, direct=_FakeEnterpriseAiClient(_DIRECT_SQL, fail=True)
    )

    with pytest.raises(RuntimeError, match="Enterprise AI から応答がありません"):
        _generate(service, Nl2SqlEngine.ENTERPRISE_AI_DIRECT)


def test_unconfigured_enterprise_ai_is_an_error_in_oracle_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _oracle_runtime_service(
        monkeypatch, direct=_FakeEnterpriseAiClient(_DIRECT_SQL, configured=False)
    )

    with pytest.raises(RuntimeError, match="構成されていません"):
        _generate(service, Nl2SqlEngine.ENTERPRISE_AI_DIRECT)

    engine_timings: list[dict[str, object]] = []
    with pytest.raises(RuntimeError, match="構成されていません"):
        service._generate_sql(  # noqa: SLF001
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "社員一覧を確認したい",
            service.get_profile(None),
            AllowedObjects(),
            10,
            [],
            allow_deterministic_fallback=False,
            engine_timings=engine_timings,
        )
    assert len(engine_timings) == 1
    assert engine_timings[0]["engine"] == "enterprise_ai_direct"
    assert engine_timings[0]["status"] == "skipped"
    assert engine_timings[0]["error"] == "OCI Enterprise AI Direct が構成されていません。"
    assert isinstance(engine_timings[0]["elapsed_ms"], int)
    assert engine_timings[0]["elapsed_ms"] >= 0


def test_deterministic_runtime_keeps_template_fallback_for_demo() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _catalog()  # noqa: SLF001
    service._enterprise_ai_client = _FakeEnterpriseAiClient(  # noqa: SLF001
        _DIRECT_SQL, configured=False
    )

    generated = _generate(service, Nl2SqlEngine.SELECT_AI)

    # local/CI では Oracle を呼ばずにテンプレート SQL でデモできる(従来どおり)。
    assert generated.engine == Nl2SqlEngine.SELECT_AI
    assert generated.generated_sql.upper().startswith("SELECT")
    assert generated.fallback_reason == ""
    assert generated.engine_meta["engine_timings"] == [
        {
            "engine": "deterministic",
            "elapsed_ms": generated.engine_meta["generation_elapsed_ms"],
            "status": "success",
            "error": "",
        }
    ]
