"""不適合の現在版からの明示の修復を、合成の行で確かめる(#4697)。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from rag_parser_core.language import build_pack, builtin_pack

from app.clients.oracle import OracleClient
from app.rag import language_pack_repair as repair
from app.rag import language_pack_repair_cli as cli
from app.rag.language_pack_retired_fields import RETIRED_QUESTION_CUE_FIELDS
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline

_CUSTOM = "合成の保存済み文言"
_MISSING = ("answer_labels.applications_section", "grounding_cues.citation_unit_words")


def _effective() -> dict[str, Any]:
    effective = builtin_pack().to_dict()
    effective["answer_labels"]["gaps_section"] = _CUSTOM
    for name in _MISSING:
        section, key = name.split(".")
        del effective[section][key]
    return effective


def _release_row(effective: dict[str, Any] | None = None) -> dict[str, object]:
    return {
        "release_id": "release-a",
        "version": 1,
        "effective_json": _effective() if effective is None else effective,
        "content_hash": "0" * 16,
        "ingest_hash": builtin_pack().ingest_hash,
        "published_at": datetime.now(UTC),
    }


def _state_row(draft: dict[str, Any] | None = None) -> dict[str, object]:
    return {
        "draft_revision": 3,
        "draft_effective_json": _effective() if draft is None else draft,
        "current_release_id": "release-a",
    }


def _complete() -> dict[str, Any]:
    complete = builtin_pack().to_dict()
    complete["answer_labels"]["gaps_section"] = _CUSTOM
    return complete


def _client(*rows: list[dict[str, object]]) -> tuple[OracleClient, FakeOraclePool]:
    pool = FakeOraclePool(execute_results=list(rows))
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), pool


def _writes(pool: FakeOraclePool) -> list[str]:
    return [
        call.statement for call in pool.connection.calls if not call.statement.startswith("SELECT")
    ]


def _readback(uuid: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """発行した版を読み戻す行(候補は組み込みの値と独自の値から独立に作る)。"""
    pack = build_pack(_complete())
    return (
        [{"draft_revision": 4, "draft_effective_json": pack.to_dict(), "current_release_id": uuid}],
        [
            {
                "release_id": uuid,
                "version": 2,
                "effective_json": pack.to_dict(),
                "content_hash": pack.hash,
                "ingest_hash": pack.ingest_hash,
                "published_at": datetime.now(UTC),
                "previous_id": "release-a",
            }
        ],
    )


async def test_dry_run_reads_only_and_reports_names() -> None:
    oracle, pool = _client([_state_row()], [_release_row()], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.blockers == []
    assert plan.current_release_id == "release-a"
    assert plan.draft_revision == 3
    assert plan.current_conforms is False
    assert plan.draft_conforms is False
    assert plan.draft_matches_current is True
    assert plan.draft_changes_kept is False
    assert plan.missing_fields == sorted(_MISSING)
    assert plan.candidate_valid and plan.ingest_hash_unchanged
    assert _writes(pool) == []
    assert all("FOR UPDATE" not in call.statement for call in pool.connection.calls)


_DRAFT_CHANGE = "合成の下書きの文言"


def _changed_draft() -> dict[str, Any]:
    draft = _effective()
    draft["answer_labels"]["gaps_section"] = _DRAFT_CHANGE
    return draft


async def test_draft_changes_are_kept_when_current_is_repaired() -> None:
    """公開版も下書きも不適合で下書きに未公開の変更があるとき、止めずに下書きの値を保つ(#4832)。"""
    oracle, pool = _client([_state_row(_changed_draft())], [_release_row()], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.repair_target == repair.REPAIR_TARGET_CURRENT
    assert plan.draft_matches_current is False
    assert plan.draft_changes_kept is True
    assert plan.blockers == []
    assert _writes(pool) == []


async def test_apply_keeps_unpublished_draft_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """発行する版は公開版の内容、下書きは未公開の変更を保ったまま欠けた項目だけ補った内容(#4832)。"""
    monkeypatch.setattr(repair, "uuid4", lambda: SimpleNamespace(hex="release-b"))
    kept = _complete()
    kept["answer_labels"]["gaps_section"] = _DRAFT_CHANGE
    kept = build_pack(kept).to_dict()
    state_back, release_back = _readback("release-b")
    state_back[0]["draft_effective_json"] = kept
    oracle, pool = _client(
        [_state_row(_changed_draft())], [_release_row()], [{"total": 0}], state_back, release_back
    )
    result = await repair.apply_language_pack_repair(
        oracle, expected_current_id="release-a", expected_draft_revision=3
    )
    assert result.release_id == "release-b" and result.draft_revision == 4
    assert pool.connection.commits == 1 and pool.connection.rollbacks == 0
    insert, update = _writes(pool)
    inserted = next(c.parameters for c in pool.connection.calls if c.statement == insert)
    assert inserted["hash"] == build_pack(_complete()).hash
    bound = next(c.parameters for c in pool.connection.calls if c.statement == update)["effective"]
    written = json.loads(bound) if isinstance(bound, (str, bytes)) else bound
    assert written == kept
    assert written["answer_labels"]["gaps_section"] == _DRAFT_CHANGE
    assert written != build_pack(_complete()).to_dict()


def _with_extra() -> dict[str, Any]:
    effective = _effective()
    effective["answer_labels"]["unknown_key"] = "合成の余剰項目"
    return effective


def _non_mapping_section() -> dict[str, Any]:
    effective = _effective()
    effective["answer_labels"] = "合成の壊れた節"
    return effective


def _bad_value() -> dict[str, Any]:
    effective = _effective()
    effective["answer_labels"]["gaps_section"] = 3
    return effective


@pytest.mark.parametrize("make", [_with_extra, _non_mapping_section, _bad_value])
async def test_blocks_when_candidate_fails_strict_validation(make: Any) -> None:
    oracle, pool = _client([_state_row()], [_release_row(make())], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert repair.CANDIDATE_INVALID in plan.blockers
    assert plan.candidate_valid is False
    assert _writes(pool) == []


async def test_blocks_when_current_already_conforms() -> None:
    pack = builtin_pack()
    row = {
        **_release_row(pack.to_dict()),
        "content_hash": pack.hash,
        "ingest_hash": pack.ingest_hash,
    }
    oracle, _ = _client([_state_row(pack.to_dict())], [row], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.current_conforms is True
    assert plan.blockers == [repair.CURRENT_ALREADY_CONFORMS]


async def test_blocks_complete_content_with_broken_hash() -> None:
    """項目が揃っているのに照合できない保存物は、発行で正にしない。"""
    oracle, _ = _client(
        [_state_row(_complete())],
        [_release_row(_complete())],  # content_hash は実際の内容と合わない
        [{"total": 0}],
    )
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.missing_fields == []
    assert plan.blockers == [repair.NO_FIELDS_TO_REPAIR]


async def test_blocks_when_ingest_hash_would_change() -> None:
    row = {**_release_row(), "ingest_hash": "f" * 16}
    oracle, _ = _client([_state_row()], [row], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.ingest_hash_unchanged is False
    assert plan.blockers == [repair.INGEST_HASH_CHANGED]


async def test_blocks_while_ingestion_jobs_are_active() -> None:
    oracle, pool = _client([_state_row()], [_release_row()], [{"total": 2}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.active_ingestion_jobs == 2
    assert plan.blockers == [repair.ACTIVE_INGESTION_JOBS]
    assert "UPPER(status) IN ('QUEUED', 'RUNNING')" in pool.connection.calls[-1].statement


async def test_draft_that_is_not_an_object_blocks_without_crashing() -> None:
    oracle, _ = _client([_state_row(["壊れた下書き"])], [_release_row()], [{"total": 0}])  # type: ignore[arg-type]
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.draft_conforms is False
    assert plan.blockers == [repair.DRAFT_DIFFERS_FROM_CURRENT]


async def test_retired_fields_are_removed_like_the_saved_data_migration() -> None:
    retired = sorted(RETIRED_QUESTION_CUE_FIELDS)[0]
    effective = _effective()
    effective["question_cues"][retired] = ["合成語"]
    oracle, _ = _client([_state_row()], [_release_row(effective)], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.retired_fields == [f"question_cues.{retired}"]
    assert plan.blockers == []


async def test_missing_state_or_release_row_is_not_repaired() -> None:
    oracle, _ = _client([])
    with pytest.raises(ValueError, match="状態行"):
        await repair.inspect_language_pack_repair(oracle)
    oracle, _ = _client([_state_row()], [])
    with pytest.raises(ValueError, match="現在の公開版"):
        await repair.inspect_language_pack_repair(oracle)


async def test_apply_publishes_complete_release_and_switches_pointers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repair, "uuid4", lambda: SimpleNamespace(hex="release-b"))
    old = _release_row()
    untouched = deepcopy(old)
    state_back, release_back = _readback("release-b")
    oracle, pool = _client([_state_row()], [old], [{"total": 0}], state_back, release_back)
    result = await repair.apply_language_pack_repair(
        oracle, expected_current_id="release-a", expected_draft_revision=3
    )
    assert result.release_id == "release-b" and result.version == 2
    assert result.previous_release_id == "release-a" and result.draft_revision == 4
    assert result.filled_fields == sorted(_MISSING)
    assert "FOR UPDATE" in pool.connection.calls[0].statement
    assert pool.connection.commits == 1 and pool.connection.rollbacks == 0

    insert, update = _writes(pool)
    assert insert.lstrip().startswith("INSERT INTO rag_language_pack_releases")
    assert update.startswith("UPDATE rag_language_pack_state")
    assert "current_release_id" in update and "draft_effective_json" in update
    inserted = next(c.parameters for c in pool.connection.calls if c.statement == insert)
    complete = build_pack(_complete())
    assert inserted["previous_id"] == "release-a"
    assert inserted["version"] == 2
    assert inserted["hash"] == complete.hash
    assert inserted["ingest_hash"] == complete.ingest_hash
    assert old == untouched


@pytest.mark.parametrize(
    ("current_id", "revision", "code"),
    [
        ("release-other", 3, repair.EXPECTED_CURRENT_MISMATCH),
        ("release-a", 2, repair.EXPECTED_DRAFT_REVISION_MISMATCH),
    ],
)
async def test_apply_stops_on_expected_mismatch(current_id: str, revision: int, code: str) -> None:
    oracle, pool = _client([_state_row()], [_release_row()], [{"total": 0}])
    with pytest.raises(repair.LanguagePackRepairBlocked) as caught:
        await repair.apply_language_pack_repair(
            oracle, expected_current_id=current_id, expected_draft_revision=revision
        )
    assert caught.value.plan.blockers == [code]
    assert _writes(pool) == []
    assert pool.connection.commits == 0 and pool.connection.rollbacks == 1


async def test_apply_rechecks_blockers_inside_the_transaction() -> None:
    oracle, pool = _client([_state_row()], [_release_row()], [{"total": 1}])
    with pytest.raises(repair.LanguagePackRepairBlocked):
        await repair.apply_language_pack_repair(
            oracle, expected_current_id="release-a", expected_draft_revision=3
        )
    assert _writes(pool) == [] and pool.connection.commits == 0


@pytest.mark.parametrize("case", ["other_pointer", "broken_readback"])
async def test_apply_rolls_back_when_readback_fails(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repair, "uuid4", lambda: SimpleNamespace(hex="release-b"))
    state_back, release_back = _readback("release-b")
    if case == "other_pointer":
        state_back[0]["current_release_id"] = "release-a"
        release_back[0]["release_id"] = "release-a"
    else:
        broken = release_back[0]["effective_json"]
        assert isinstance(broken, dict)
        del broken["answer_labels"]["applications_section"]
    oracle, pool = _client(
        [_state_row()], [_release_row()], [{"total": 0}], state_back, release_back
    )
    with pytest.raises(ValueError, match="読み戻し|契約"):
        await repair.apply_language_pack_repair(
            oracle, expected_current_id="release-a", expected_draft_revision=3
        )
    assert pool.connection.commits == 0 and pool.connection.rollbacks == 1


def _patch_cli(monkeypatch: pytest.MonkeyPatch, oracle: OracleClient) -> None:
    monkeypatch.setattr(cli, "OracleClient", lambda *_args, **_kwargs: oracle)
    monkeypatch.setattr(cli, "get_settings", lambda: object())


def test_cli_dry_run_prints_names_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle, pool = _client([_state_row()], [_release_row()], [{"total": 0}])
    _patch_cli(monkeypatch, oracle)
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["mode"] == "dry-run" and payload["status"] == "ready"
    assert payload["missing_fields"] == sorted(_MISSING)
    builtin = builtin_pack().to_dict()
    assert _CUSTOM not in captured.out
    assert builtin["answer_labels"]["applications_section"] not in captured.out
    assert all(
        word not in captured.out for word in builtin["grounding_cues"]["citation_unit_words"]
    )
    assert _writes(pool) == []


def test_cli_blocked_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle, _ = _client([_state_row()], [_release_row()], [{"total": 1}])
    _patch_cli(monkeypatch, oracle)
    assert cli.main([]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["blockers"] == [repair.ACTIVE_INGESTION_JOBS]


def test_cli_apply_reports_new_release(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(repair, "uuid4", lambda: SimpleNamespace(hex="release-b"))
    state_back, release_back = _readback("release-b")
    oracle, _ = _client([_state_row()], [_release_row()], [{"total": 0}], state_back, release_back)
    _patch_cli(monkeypatch, oracle)
    code = cli.main(
        ["--apply", "--expected-current-id", "release-a", "--expected-draft-revision", "3"]
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "applied" and payload["release_id"] == "release-b"
    assert _CUSTOM not in captured.out


def test_cli_snapshot_error_does_not_print_cause(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle, _ = _client([])
    _patch_cli(monkeypatch, oracle)
    assert cli.main([]) == 1
    captured = capsys.readouterr()
    # 標準エラーには CLI の構造化ログの行も混ざるので、JSON 全体としては読まない。
    assert '"error_code": "LANGUAGE_PACK_REPAIR_UNAVAILABLE"' in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply"],
        ["--apply", "--expected-current-id", "release-a"],
        ["--apply", "--expected-draft-revision", "3"],
        ["--expected-current-id", "release-a"],
        ["--expected-draft-revision", "3"],
    ],
)
def test_cli_requires_expected_values_only_with_apply(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("引数の誤りでは接続しない")

    monkeypatch.setattr(cli, "OracleClient", fail)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


# 公開版は適合で、下書きだけが不適合の環境の修復(#4745)。
_PUBLISHED_ONLY = "合成の公開版の文言"
_DRAFT_ONLY = "合成の下書きの文言"
_DRAFT_REPAIR_MISSING = "answer_labels.applications_section"


def _published() -> dict[str, Any]:
    published = _complete()
    published["answer_labels"]["applications_section"] = _PUBLISHED_ONLY
    return published


def _published_row() -> dict[str, object]:
    pack = build_pack(_published())
    return {
        "release_id": "release-a",
        "version": 7,
        "effective_json": pack.to_dict(),
        "content_hash": pack.hash,
        "ingest_hash": pack.ingest_hash,
        "published_at": datetime.now(UTC),
    }


def _partial_draft() -> dict[str, Any]:
    """利用者の未公開の変更を持ち、1 項目が欠けた下書き。"""
    draft = _complete()
    draft["answer_labels"]["gaps_section"] = _DRAFT_ONLY
    del draft["answer_labels"]["applications_section"]
    return draft


def _repaired_draft() -> dict[str, Any]:
    """欠けた項目は公開版の値、それ以外は下書きの値のままの、独立に作った期待値。"""
    repaired = _complete()
    repaired["answer_labels"]["gaps_section"] = _DRAFT_ONLY
    repaired["answer_labels"]["applications_section"] = _PUBLISHED_ONLY
    return repaired


def _draft_readback() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    state = {
        "draft_revision": 4,
        "draft_effective_json": build_pack(_repaired_draft()).to_dict(),
        "current_release_id": "release-a",
    }
    return [state], [_published_row()]


async def test_draft_only_dry_run_plans_draft_repair_without_writing() -> None:
    oracle, pool = _client([_state_row(_partial_draft())], [_published_row()], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.repair_target == repair.REPAIR_TARGET_DRAFT
    assert plan.blockers == []
    assert plan.current_conforms is True and plan.draft_conforms is False
    assert plan.draft_matches_current is False
    assert plan.missing_fields == [_DRAFT_REPAIR_MISSING]
    assert plan.retired_fields == []
    assert plan.candidate_valid is True
    assert _writes(pool) == []
    assert all("FOR UPDATE" not in call.statement for call in pool.connection.calls)


async def test_draft_repair_removes_retired_fields_and_sections() -> None:
    retired = sorted(RETIRED_QUESTION_CUE_FIELDS)[0]
    draft = _partial_draft()
    draft["question_cues"][retired] = ["合成語"]
    draft["guide_check_cues"] = {"approval_terms": ["合成語"]}
    oracle, _ = _client([_state_row(draft)], [_published_row()], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.blockers == []
    assert plan.retired_fields == sorted(
        [f"question_cues.{retired}", "guide_check_cues.approval_terms"]
    )


async def test_draft_repair_is_not_blocked_by_running_jobs() -> None:
    """下書きの修復は公開版を変えないので、取込の実行中でも止めない。"""
    oracle, _ = _client([_state_row(_partial_draft())], [_published_row()], [{"total": 2}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.active_ingestion_jobs == 2
    assert plan.blockers == []


def _draft_with_extra() -> dict[str, Any]:
    draft = _partial_draft()
    draft["answer_labels"]["unknown_key"] = "合成の余剰項目"
    return draft


def _draft_with_bad_value() -> dict[str, Any]:
    draft = _partial_draft()
    draft["answer_labels"]["gaps_section"] = 3
    return draft


def _draft_with_non_mapping_section() -> dict[str, Any]:
    draft = _partial_draft()
    draft["answer_labels"] = "合成の壊れた節"
    return draft


def _draft_with_other_locale() -> dict[str, Any]:
    return {**_partial_draft(), "locale": "synthetic"}


def _draft_without_locale() -> dict[str, Any]:
    draft = _partial_draft()
    del draft["locale"]
    return draft


@pytest.mark.parametrize(
    "make",
    [
        _draft_with_extra,
        _draft_with_bad_value,
        _draft_with_non_mapping_section,
        _draft_with_other_locale,
        _draft_without_locale,
        lambda: ["合成の壊れた下書き"],
    ],
)
async def test_draft_that_cannot_be_completed_is_not_replaced(make: Any) -> None:
    """機械的に補えない下書きを、公開版や組み込みの値で置き換えて消さない。"""
    oracle, pool = _client([_state_row(make())], [_published_row()], [{"total": 0}])
    with pytest.raises(repair.LanguagePackRepairBlocked) as caught:
        await repair.apply_language_pack_repair(
            oracle, expected_current_id="release-a", expected_draft_revision=3
        )
    assert caught.value.plan.repair_target == repair.REPAIR_TARGET_DRAFT
    assert caught.value.plan.blockers == [repair.CANDIDATE_INVALID]
    assert _writes(pool) == [] and pool.connection.commits == 0


async def test_apply_updates_only_the_draft_and_keeps_user_values() -> None:
    state_back, release_back = _draft_readback()
    oracle, pool = _client(
        [_state_row(_partial_draft())],
        [_published_row()],
        [{"total": 0}],
        state_back,
        release_back,
    )
    result = await repair.apply_language_pack_repair(
        oracle, expected_current_id="release-a", expected_draft_revision=3
    )
    assert result.repair_target == repair.REPAIR_TARGET_DRAFT
    assert result.previous_release_id == result.release_id == "release-a"
    assert result.version == 7 and result.draft_revision == 4
    assert result.filled_fields == [_DRAFT_REPAIR_MISSING]
    assert "FOR UPDATE" in pool.connection.calls[0].statement
    assert pool.connection.commits == 1 and pool.connection.rollbacks == 0

    [update] = _writes(pool)
    assert update.startswith("UPDATE rag_language_pack_state")
    assert "draft_revision=draft_revision+1" in update
    assert "current_release_id" not in update
    written = next(c.parameters for c in pool.connection.calls if c.statement == update)
    assert json.loads(str(written["effective"])) == _repaired_draft()


@pytest.mark.parametrize(
    ("current_id", "revision", "code"),
    [
        ("release-other", 3, repair.EXPECTED_CURRENT_MISMATCH),
        ("release-a", 2, repair.EXPECTED_DRAFT_REVISION_MISMATCH),
    ],
)
async def test_draft_apply_stops_on_expected_mismatch(
    current_id: str, revision: int, code: str
) -> None:
    oracle, pool = _client([_state_row(_partial_draft())], [_published_row()], [{"total": 0}])
    with pytest.raises(repair.LanguagePackRepairBlocked) as caught:
        await repair.apply_language_pack_repair(
            oracle, expected_current_id=current_id, expected_draft_revision=revision
        )
    assert caught.value.plan.blockers == [code]
    assert _writes(pool) == [] and pool.connection.commits == 0


@pytest.mark.parametrize("case", ["other_pointer", "other_content", "broken_readback"])
async def test_draft_apply_rolls_back_when_readback_fails(case: str) -> None:
    state_back, release_back = _draft_readback()
    effective = state_back[0]["draft_effective_json"]
    assert isinstance(effective, dict)
    if case == "other_pointer":
        state_back[0]["current_release_id"] = "release-b"
        release_back[0]["release_id"] = "release-b"
    elif case == "other_content":
        effective["answer_labels"]["gaps_section"] = "合成の別の文言"
    else:
        del effective["answer_labels"]["applications_section"]
    oracle, pool = _client(
        [_state_row(_partial_draft())],
        [_published_row()],
        [{"total": 0}],
        state_back,
        release_back,
    )
    with pytest.raises(ValueError, match="読み戻し|契約|完全"):
        await repair.apply_language_pack_repair(
            oracle, expected_current_id="release-a", expected_draft_revision=3
        )
    assert pool.connection.commits == 0 and pool.connection.rollbacks == 1


async def test_nonconforming_current_stays_the_repair_target() -> None:
    """公開版が不適合なら、下書きが不適合でも公開版の修復のままにする。"""
    oracle, _ = _client([_state_row()], [_release_row()], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.repair_target == repair.REPAIR_TARGET_CURRENT
    assert plan.blockers == []


async def test_nothing_to_repair_when_both_conform() -> None:
    oracle, _ = _client(
        [_state_row(build_pack(_published()).to_dict())], [_published_row()], [{"total": 0}]
    )
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.repair_target == repair.REPAIR_TARGET_NONE
    assert plan.blockers == [repair.CURRENT_ALREADY_CONFORMS]


async def test_retired_section_in_nonconforming_current_is_reported() -> None:
    effective = _effective()
    effective["guide_check_cues"] = {"approval_terms": ["合成語"]}
    oracle, _ = _client([_state_row()], [_release_row(effective)], [{"total": 0}])
    plan = await repair.inspect_language_pack_repair(oracle)
    assert plan.retired_fields == ["guide_check_cues.approval_terms"]
    assert plan.blockers == []


def test_cli_draft_dry_run_prints_names_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle, pool = _client([_state_row(_partial_draft())], [_published_row()], [{"total": 0}])
    _patch_cli(monkeypatch, oracle)
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["mode"] == "dry-run" and payload["status"] == "ready"
    assert payload["repair_target"] == "draft"
    assert payload["missing_fields"] == [_DRAFT_REPAIR_MISSING]
    assert _DRAFT_ONLY not in captured.out and _PUBLISHED_ONLY not in captured.out
    assert _writes(pool) == []


def test_cli_draft_apply_reports_without_new_release(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_back, release_back = _draft_readback()
    oracle, pool = _client(
        [_state_row(_partial_draft())],
        [_published_row()],
        [{"total": 0}],
        state_back,
        release_back,
    )
    _patch_cli(monkeypatch, oracle)
    code = cli.main(
        ["--apply", "--expected-current-id", "release-a", "--expected-draft-revision", "3"]
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "applied" and payload["repair_target"] == "draft"
    assert payload["release_id"] == "release-a" and payload["draft_revision"] == 4
    assert _DRAFT_ONLY not in captured.out and _PUBLISHED_ONLY not in captured.out
    assert not any("INSERT" in statement for statement in _writes(pool))
