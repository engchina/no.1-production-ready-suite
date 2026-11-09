"""現在版か下書きが保存契約に合わない環境の、明示の修復(#4697・#4745)。

通常の保存・公開・復元は書き込みの前に現在版と下書きを厳格に検証するので、不適合の状態からは直せない。
この経路だけが保存物を厳格な検証なしに読み、明示の操作で次のどちらかを行う。

- 現在版が不適合: 欠けた項目だけ組み込みの値を足した、完全な内容の新しい公開版を発行する。
- 下書きだけが不適合: 欠けた項目だけ現在版の値を足し、下書きだけを書き換える(公開しない)。

起動・API・読み取りの処理からは呼ばない(互換の補完や自動の公開にしない)。旧版の内容と hash は
書き換えない。配備の確認の読み取りだけの検査(#4695)も、同じ読み取りを使うのでここに置く。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from rag_parser_core.language import (
    FIELD_SPECS,
    LanguagePack,
    LanguagePackError,
    build_pack,
    builtin_pack,
)

from app.clients import language_pack_release_store as release_store
from app.rag.language_pack_contract import (
    LanguagePackNotInitializedError,
    LanguagePackSnapshotError,
)
from app.rag.language_pack_retired_fields import without_retired_fields
from app.schemas.language_pack_release import LanguagePackRelease

if TYPE_CHECKING:
    from app.clients.oracle import OracleClient, OracleConnectionProtocol

INGESTION_JOBS_TABLE = "rag_ingestion_jobs"
PUBLISHED_BY = "language-pack-repair-cli"

CURRENT_ALREADY_CONFORMS = "CURRENT_ALREADY_CONFORMS"
NO_FIELDS_TO_REPAIR = "NO_FIELDS_TO_REPAIR"
CANDIDATE_INVALID = "CANDIDATE_INVALID"
INGEST_HASH_CHANGED = "INGEST_HASH_CHANGED"
ACTIVE_INGESTION_JOBS = "ACTIVE_INGESTION_JOBS"
DRAFT_DIFFERS_FROM_CURRENT = "DRAFT_DIFFERS_FROM_CURRENT"
EXPECTED_CURRENT_MISMATCH = "EXPECTED_CURRENT_MISMATCH"
EXPECTED_DRAFT_REVISION_MISMATCH = "EXPECTED_DRAFT_REVISION_MISMATCH"

REPAIR_TARGET_CURRENT = "current"
REPAIR_TARGET_DRAFT = "draft"
REPAIR_TARGET_NONE = "none"

_METADATA_KEYS = ("locale", "version")
_UNDETERMINED_FIELD = "（項目を特定できない形式の誤り）"


@dataclass(frozen=True)
class LanguagePackRepairPlan:
    """修復の判定の結果。項目の名前だけを持ち、言語資源の値は持たない。"""

    repair_target: str
    current_release_id: str
    current_version: int
    draft_revision: int
    current_conforms: bool
    draft_conforms: bool
    draft_matches_current: bool
    missing_fields: list[str]
    retired_fields: list[str]
    candidate_valid: bool
    ingest_hash_unchanged: bool
    active_ingestion_jobs: int
    blockers: list[str]


@dataclass(frozen=True)
class LanguagePackRepairResult:
    """適用の結果。下書きの修復では新しい版を作らないので、前後の release は同じ現在版になる。"""

    repair_target: str
    previous_release_id: str
    release_id: str
    version: int
    draft_revision: int
    filled_fields: list[str]
    removed_retired_fields: list[str]


@dataclass(frozen=True)
class LanguagePackContractIssues:
    """今の公開版と下書きの、現行の契約に合わない項目の名前。言語資源の値は持たない。"""

    current: list[str]
    draft: list[str]


class LanguagePackRepairBlocked(ValueError):
    """止める条件に当たった。transaction は書き込まずに rollback する。"""

    def __init__(self, plan: LanguagePackRepairPlan) -> None:
        super().__init__("言語資源の修復の条件を満たしません。")
        self.plan = plan


def _passes(check: Callable[[], object]) -> bool:
    try:
        check()
    except (ValueError, TypeError, AttributeError):
        # build_pack は節の形が dict でないと TypeError / AttributeError になる。
        return False
    return True


def _without_retired(pack: Any) -> Any:
    return (without_retired_fields(pack) or pack) if isinstance(pack, Mapping) else pack


def _candidate(
    stored: Any, *, fill_from: Mapping[str, Any] | None = None
) -> tuple[LanguagePack | None, list[str], list[str]]:
    """欠けた項目だけ足し、(候補、足した項目、除いた廃止の項目) を返す。

    足す値は fill_from(適合している現在版)から取り、無ければ組み込みの値を使う。
    """
    if not isinstance(stored, Mapping) or not isinstance(stored.get("locale"), str):
        return None, [], []
    base = without_retired_fields(stored) or stored
    if any(
        not isinstance(fields, Mapping)
        for section, fields in base.items()
        if section not in ("locale", "version")
    ):
        return None, [], []
    if fill_from is not None and base["locale"] != fill_from.get("locale"):
        # 別の言語の値を混ぜない。
        return None, [], []
    try:
        source = builtin_pack(base["locale"]).to_dict() if fill_from is None else fill_from
    except (OSError, ValueError):
        return None, [], []
    candidate: dict[str, Any] = {
        section: dict(fields) if isinstance(fields, Mapping) else fields
        for section, fields in base.items()
    }
    retired = sorted(
        f"{section}.{key}"
        for section, fields in stored.items()
        if isinstance(fields, Mapping)
        for key in fields
        if key not in base.get(section, {})
    )
    missing: list[str] = []
    for spec in FIELD_SPECS:
        fields = candidate.setdefault(spec.section, {})
        if spec.key not in fields:
            fields[spec.key] = source[spec.section][spec.key]
            missing.append(f"{spec.section}.{spec.key}")
    try:
        pack = release_store._validated(candidate)
    except (ValueError, TypeError, AttributeError):
        return None, sorted(missing), retired
    return pack, sorted(missing), retired


def _stored_rows(
    connection: OracleConnectionProtocol, *, lock: bool
) -> tuple[dict[str, object], dict[str, object]]:
    """不適合の現在版を読むための、厳格な検証を通さない読み取り。(状態の行、現在版の行) を返す。"""
    from app.clients.oracle import _fetch_one

    state_row = _fetch_one(
        connection,
        "SELECT draft_revision, draft_effective_json, current_release_id "
        f"FROM {release_store.STATE_TABLE} WHERE singleton_id=1" + (" FOR UPDATE" if lock else ""),
        {},
    )
    if state_row is None or not state_row.get("current_release_id"):
        raise LanguagePackSnapshotError("言語資源の状態行または現在版ポインタがありません。")
    release_row = _fetch_one(
        connection,
        f"SELECT * FROM {release_store.RELEASE_TABLE} WHERE release_id=:id",
        {"id": state_row["current_release_id"]},
    )
    if release_row is None:
        raise LanguagePackSnapshotError("言語資源の現在の公開版が見つかりません。")
    return state_row, release_row


def _inspect(
    connection: OracleConnectionProtocol, *, lock: bool
) -> tuple[LanguagePackRepairPlan, LanguagePack | None]:
    from app.clients.oracle import _fetch_one, _json_loads

    state_row, release_row = _stored_rows(connection, lock=lock)
    effective = _json_loads(release_row["effective_json"])
    draft = _json_loads(state_row["draft_effective_json"])
    jobs_row = _fetch_one(
        connection,
        f"SELECT COUNT(*) total FROM {INGESTION_JOBS_TABLE} "
        "WHERE UPPER(status) IN ('QUEUED', 'RUNNING')",
        {},
    )
    active_jobs = int(str(jobs_row["total"])) if jobs_row else 0
    current_conforms = _passes(lambda: release_store._release(release_row))
    draft_conforms = _passes(lambda: release_store._validated(draft))
    target = (
        REPAIR_TARGET_CURRENT
        if not current_conforms
        else REPAIR_TARGET_DRAFT
        if not draft_conforms
        else REPAIR_TARGET_NONE
    )
    pack, missing, retired = (
        _candidate(draft, fill_from=effective)
        if target == REPAIR_TARGET_DRAFT
        else _candidate(effective)
    )
    ingest_unchanged = pack is not None and pack.ingest_hash == release_row["ingest_hash"]
    # 廃止した項目だけの違いは未公開の変更ではない。
    draft_matches = _without_retired(draft) == _without_retired(effective)

    blockers: list[str] = []
    if target == REPAIR_TARGET_DRAFT:
        # 下書きの修復は公開版を変えず、下書きの値も保つので、公開版の修復の条件は掛けない。
        if pack is None:
            blockers.append(CANDIDATE_INVALID)
    else:
        if current_conforms:
            blockers.append(CURRENT_ALREADY_CONFORMS)
        elif not missing and not retired:
            # 項目は揃っているのに不適合(hash の不一致など)。発行すると不正な保存物を正にする。
            blockers.append(NO_FIELDS_TO_REPAIR)
        if pack is None:
            blockers.append(CANDIDATE_INVALID)
        elif not ingest_unchanged:
            blockers.append(INGEST_HASH_CHANGED)
        if active_jobs:
            blockers.append(ACTIVE_INGESTION_JOBS)
        if not draft_matches:
            # 適用は下書きを新しい版の内容に置き換えるので、未公開の変更を黙って消さない。
            blockers.append(DRAFT_DIFFERS_FROM_CURRENT)
    plan = LanguagePackRepairPlan(
        repair_target=target,
        current_release_id=str(release_row["release_id"]),
        current_version=int(str(release_row["version"])),
        draft_revision=int(str(state_row["draft_revision"])),
        current_conforms=current_conforms,
        draft_conforms=draft_conforms,
        draft_matches_current=draft_matches,
        missing_fields=missing,
        retired_fields=retired,
        candidate_valid=pack is not None,
        ingest_hash_unchanged=ingest_unchanged,
        active_ingestion_jobs=active_jobs,
        blockers=blockers,
    )
    return plan, pack


def _snapshot_issues(effective: Mapping[str, Any]) -> list[str]:
    """保存内容が現行の契約に合わない項目の名前を返す(例外の文には理由や値が入るので使わない)。"""
    malformed = {
        section
        for section, fields in effective.items()
        if section not in _METADATA_KEYS and not isinstance(fields, Mapping)
    }
    sections = {key: value for key, value in effective.items() if key not in malformed}
    try:
        normalized = build_pack(sections).to_dict()
    except LanguagePackError as exc:
        return sorted(malformed | set(exc.errors))
    except (TypeError, AttributeError):
        return sorted(malformed | {_UNDETERMINED_FIELD})
    # 検証は通るが、保存の形が現行とそろわない項目(release_store._validated の完全一致)。
    differing: set[str] = set()
    for section in sections.keys() | normalized.keys():
        stored, expected = sections.get(section), normalized.get(section)
        if stored == expected:
            continue
        if isinstance(stored, Mapping) and isinstance(expected, Mapping):
            differing.update(
                f"{section}.{key}"
                for key in stored.keys() | expected.keys()
                if stored.get(key) != expected.get(key)
            )
        else:
            differing.add(section)
    return sorted(malformed | differing)


def _current_release_issues(row: Mapping[str, object], effective: Mapping[str, Any]) -> list[str]:
    issues = _snapshot_issues(effective)
    if issues:
        return issues
    pack = build_pack(effective)
    return [
        column
        for column, computed in (("content_hash", pack.hash), ("ingest_hash", pack.ingest_hash))
        if row.get(column) != computed
    ]


async def inspect_language_pack_contract(oracle: OracleClient) -> LanguagePackContractIssues | None:
    """配備の確認の読み取りだけの検査(#4695)。今の公開版と下書きだけを見る。

    実行時と同じ厳格な読み取りで判定し、合うとき・公開版が 1 つも無いときは None を返す。合わない
    ときだけ項目の名前を調べる。状態の行や現在版ポインタの不整合は LanguagePackSnapshotError。
    """

    def operation(connection: OracleConnectionProtocol) -> LanguagePackContractIssues | None:
        from app.clients.oracle import _json_loads

        try:
            release_store._state(connection)
        except LanguagePackNotInitializedError:
            return None
        except (ValueError, TypeError, AttributeError):
            # build_pack は節の形が dict でないと TypeError / AttributeError になる。
            pass
        else:
            return None
        state_row, release_row = _stored_rows(connection, lock=False)
        issues = LanguagePackContractIssues(
            current=_current_release_issues(
                release_row, _json_loads(release_row["effective_json"])
            ),
            draft=_snapshot_issues(_json_loads(state_row["draft_effective_json"])),
        )
        if not issues.current and not issues.draft:
            # 版番号などの項目以外の不適合も、合格にしない。
            raise LanguagePackSnapshotError("言語資源の公開版の行が現在の契約に合いません。")
        return issues

    return await oracle._run_transaction(operation)


def _write_repaired_draft(
    connection: OracleConnectionProtocol, plan: LanguagePackRepairPlan, pack: LanguagePack
) -> LanguagePackRepairResult:
    """下書きだけを補った内容に書き換える。現在版のポインタと公開版には触らない。"""
    from app.clients.oracle import _execute, _json_bind, _json_input_sizes

    effective = pack.to_dict()
    _execute(
        connection,
        f"UPDATE {release_store.STATE_TABLE} SET draft_revision=draft_revision+1, "
        "draft_effective_json=:effective WHERE singleton_id=1",
        {"effective": _json_bind(effective)},
        input_sizes=_json_input_sizes("effective"),
    )
    # commit の前に厳格な検証で読み戻す。合わなければ例外で全体を rollback する。
    state = release_store._state(connection)
    if (
        state.current.id != plan.current_release_id
        or state.draft_effective != effective
        or state.draft_revision != plan.draft_revision + 1
    ):
        raise LanguagePackSnapshotError("修復した下書きの読み戻しの照合に失敗しました。")
    return LanguagePackRepairResult(
        repair_target=REPAIR_TARGET_DRAFT,
        previous_release_id=plan.current_release_id,
        release_id=plan.current_release_id,
        version=plan.current_version,
        draft_revision=state.draft_revision,
        filled_fields=plan.missing_fields,
        removed_retired_fields=plan.retired_fields,
    )


async def inspect_language_pack_repair(oracle: OracleClient) -> LanguagePackRepairPlan:
    """読み取りだけで修復の候補を判定する(lock も書き込みもしない)。"""
    return await oracle._run_transaction(lambda connection: _inspect(connection, lock=False)[0])


async def apply_language_pack_repair(
    oracle: OracleClient, *, expected_current_id: str, expected_draft_revision: int
) -> LanguagePackRepairResult:
    """dry-run で確かめた現在版と下書きの revision のままなら、修復の対象を書き換える。

    現在版が不適合なら新しい公開版へ切り替え、下書きだけが不適合なら下書きだけを書き換える。
    """

    def operation(connection: OracleConnectionProtocol) -> LanguagePackRepairResult:
        from app.clients.oracle import _execute, _json_bind, _json_input_sizes

        plan, pack = _inspect(connection, lock=True)
        blockers = list(plan.blockers)
        if plan.current_release_id != expected_current_id:
            blockers.append(EXPECTED_CURRENT_MISMATCH)
        if plan.draft_revision != expected_draft_revision:
            blockers.append(EXPECTED_DRAFT_REVISION_MISMATCH)
        if blockers or pack is None:
            raise LanguagePackRepairBlocked(replace(plan, blockers=blockers))
        if plan.repair_target == REPAIR_TARGET_DRAFT:
            return _write_repaired_draft(connection, plan, pack)
        release = LanguagePackRelease(
            id=uuid4().hex,
            version=plan.current_version + 1,
            effective=pack.to_dict(),
            hash=pack.hash,
            ingest_hash=pack.ingest_hash,
            published_at=datetime.now(UTC),
            published_by=PUBLISHED_BY,
            previous_id=plan.current_release_id,
            change_note=(
                f"保存契約の修復: 欠けていた {len(plan.missing_fields)} 項目に組み込みの値を追加、"
                f"廃止した {len(plan.retired_fields)} 項目を除去"
            ),
        )
        release_store._insert(connection, release)
        _execute(
            connection,
            f"UPDATE {release_store.STATE_TABLE} SET current_release_id=:id, "
            "draft_revision=draft_revision+1, draft_effective_json=:effective "
            "WHERE singleton_id=1",
            {"id": release.id, "effective": _json_bind(release.effective)},
            input_sizes=_json_input_sizes("effective"),
        )
        # commit の前に厳格な検証で読み戻す。合わなければ例外で全体を rollback する。
        state = release_store._state(connection)
        if (
            state.current.id != release.id
            or state.current.effective != release.effective
            or state.draft_effective != release.effective
            or state.draft_revision != plan.draft_revision + 1
        ):
            raise LanguagePackSnapshotError("発行した言語資源の読み戻しの照合に失敗しました。")
        return LanguagePackRepairResult(
            repair_target=REPAIR_TARGET_CURRENT,
            previous_release_id=plan.current_release_id,
            release_id=release.id,
            version=release.version,
            draft_revision=state.draft_revision,
            filled_fields=plan.missing_fields,
            removed_retired_fields=plan.retired_fields,
        )

    return await oracle._run_transaction(operation)
