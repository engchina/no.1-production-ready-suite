"""配備の確認（`system_schema_cli check`）の言語資源の検査を、合成の行で確かめる（#4695）。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from rag_parser_core.language import FIELD_SPECS, FieldKind, build_pack, builtin_pack

from app.clients.oracle import OracleClient
from app.rag import system_schema_cli
from app.rag.system_schema import system_schema_manager
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline

_SAVED_VALUE = "合成の保存済みの値"
_MISSING = "answer_labels.applications_section"
_DRAFT_MISSING = "grounding_cues.citation_unit_words"
_REPAIR_CLI = "app.rag.language_pack_repair_cli"
_READY = {"status": "ready", "operation_state": {"status": "idle"}}


def _complete() -> dict[str, Any]:
    effective = builtin_pack().to_dict()
    effective["answer_labels"]["gaps_section"] = _SAVED_VALUE
    return effective


def _without(name: str) -> dict[str, Any]:
    effective = _complete()
    section, key = name.split(".")
    del effective[section][key]
    return effective


def _release_row(effective: dict[str, Any], **overrides: object) -> dict[str, object]:
    pack = build_pack(_complete())
    return {
        "release_id": "release-a",
        "version": 1,
        "effective_json": effective,
        "content_hash": pack.hash,
        "ingest_hash": pack.ingest_hash,
        "published_at": datetime.now(UTC),
        **overrides,
    }


def _state_row(draft: dict[str, Any], **overrides: object) -> dict[str, object]:
    return {
        "singleton_id": 1,
        "draft_revision": 3,
        "draft_effective_json": draft,
        "current_release_id": "release-a",
        **overrides,
    }


def _run_check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    results: list[list[dict[str, object]]],
) -> tuple[int, str, FakeOraclePool]:
    pool = FakeOraclePool(execute_results=results)
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr("app.clients.oracle.OracleClient", lambda: oracle)
    monkeypatch.setattr(system_schema_manager, "status", lambda: _READY)
    code = system_schema_cli.main(["check"])
    return code, capsys.readouterr().out, pool


def _assert_read_only(pool: FakeOraclePool) -> None:
    statements = [call.statement for call in pool.connection.calls]
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert all("FOR UPDATE" not in statement for statement in statements)


def _assert_reports(output: str) -> None:
    """suite-health は最後の行を要約に使うので 1 行で出し、修復の手順を含め、値は出さない。"""
    assert len(output.strip().splitlines()) == 1
    assert _REPAIR_CLI in output
    assert "#4697・#4745" in output
    assert _SAVED_VALUE not in output


def _unread(effective: dict[str, Any], draft: dict[str, Any]) -> list[list[dict[str, object]]]:
    """厳格な読み取りが失敗し、項目を調べ直すときの行（厳格な読み取りと調べ直しで 2 回ずつ）。"""
    state, release = [_state_row(draft)], [_release_row(effective)]
    return [state, release, state, release]


def test_conforming_current_and_draft_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    complete = _complete()
    code, output, pool = _run_check(
        monkeypatch, capsys, [[_state_row(complete)], [_release_row(complete)]]
    )
    assert code == 0
    assert output.strip() == "status=ready"
    _assert_read_only(pool)


def test_environment_without_release_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """状態行も公開版も無い新しい環境は合格（初期化の前の配備を止めない）。"""
    code, output, pool = _run_check(monkeypatch, capsys, [[], []])
    assert code == 0
    assert output.strip() == "status=ready"
    _assert_read_only(pool)


def test_nonconforming_current_fails_with_field_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output, pool = _run_check(monkeypatch, capsys, _unread(_without(_MISSING), _complete()))
    assert code == 1
    assert "status=ready" in output
    assert "公開版" in output
    assert _MISSING in output
    assert "下書き" not in output
    _assert_reports(output)
    _assert_read_only(pool)


def test_nonconforming_draft_fails_with_field_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output, pool = _run_check(
        monkeypatch, capsys, _unread(_complete(), _without(_DRAFT_MISSING))
    )
    assert code == 1
    assert "下書き" in output
    assert _DRAFT_MISSING in output
    assert "公開版" not in output
    _assert_reports(output)
    _assert_read_only(pool)


def test_both_report_their_own_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output, _ = _run_check(
        monkeypatch, capsys, _unread(_without(_MISSING), _without(_DRAFT_MISSING))
    )
    assert code == 1
    current, _, draft = output.partition("下書き")
    assert _MISSING in current and _DRAFT_MISSING not in current
    assert _DRAFT_MISSING in draft and _MISSING not in draft
    _assert_reports(output)


def test_invalid_value_reports_only_the_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = next(item for item in FIELD_SPECS if item.kind is FieldKind.ENUM_MAP)
    invalid_value = "合成の不正な選択肢"
    effective = _complete()
    effective[spec.section][spec.key] = [f"合成の語={invalid_value}"]
    code, output, _ = _run_check(monkeypatch, capsys, _unread(effective, _complete()))
    assert code == 1
    assert f"{spec.section}.{spec.key}" in output
    assert invalid_value not in output
    _assert_reports(output)


def test_unnormalized_value_reports_the_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検証は通るが保存の形が現行とそろわない値（`_validated` の完全一致の失敗）も名前を出す。"""
    draft = _complete()
    draft["answer_labels"]["gaps_section"] = f"  {_SAVED_VALUE}  "
    code, output, _ = _run_check(monkeypatch, capsys, _unread(_complete(), draft))
    assert code == 1
    assert "answer_labels.gaps_section" in output
    _assert_reports(output)


def test_hash_mismatch_reports_the_column_name_without_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    complete = _complete()
    stale_hash = "f" * 16
    state, release = [_state_row(complete)], [_release_row(complete, content_hash=stale_hash)]
    code, output, _ = _run_check(monkeypatch, capsys, [state, release, state, release])
    assert code == 1
    assert "content_hash" in output
    assert stale_hash not in output
    _assert_reports(output)


def test_section_of_wrong_shape_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    draft: dict[str, Any] = deepcopy(_complete())
    draft["answer_labels"] = [_SAVED_VALUE]
    code, output, _ = _run_check(monkeypatch, capsys, _unread(_complete(), draft))
    assert code == 1
    assert "answer_labels" in output
    _assert_reports(output)


def test_undeterminable_value_shape_still_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検証そのものが例外になる形でも、合格にせず値を出さない。"""
    spec = next(item for item in FIELD_SPECS if item.kind is FieldKind.WORDS)
    draft = _complete()
    draft[spec.section][spec.key] = 90210
    code, output, _ = _run_check(monkeypatch, capsys, _unread(_complete(), draft))
    assert code == 1
    assert "90210" not in output
    _assert_reports(output)


def test_missing_current_pointer_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = [_state_row(_complete(), current_release_id=None)]
    code, output, pool = _run_check(monkeypatch, capsys, [state, state])
    assert code == 1
    assert "現在版ポインタ" in output
    _assert_reports(output)
    _assert_read_only(pool)


def test_schema_not_ready_does_not_read_language_pack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """表が無い状態で言語資源を読みに行かず、schema の要約をそのまま出す。"""

    def fail() -> OracleClient:
        raise AssertionError("schema が ready でないときは言語資源を読まない")

    monkeypatch.setattr("app.clients.oracle.OracleClient", fail)
    monkeypatch.setattr(
        system_schema_manager,
        "status",
        lambda: {"status": "missing", "operation_state": {"status": "idle"}},
    )
    assert system_schema_cli.main(["check"]) == 1
    assert capsys.readouterr().out.strip() == "status=missing"


def test_read_failure_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DB の失敗は不適合と区別し、既存の「状態を読めません」の要約で失敗にする。"""

    def fail() -> OracleClient:
        raise RuntimeError("ORA-00942: synthetic secret detail")

    monkeypatch.setattr("app.clients.oracle.OracleClient", fail)
    monkeypatch.setattr(system_schema_manager, "status", lambda: _READY)
    assert system_schema_cli.main(["check"]) == 1
    output = capsys.readouterr().out
    assert output.startswith("状態を読めません（ORA-00942")
    assert "secret" not in output
