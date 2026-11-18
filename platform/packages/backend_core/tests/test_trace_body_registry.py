"""正本の保存rollbackや削除の後にwriterが到着しても本文を作らない。"""

import copy
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

from test_trace_bodies import OwnerCursor

from pr_backend_core.observability.trace_bodies import (
    BodyOwner,
    attach_body_owner,
    prepare_body,
    release_body_owner,
)
from pr_backend_core.observability.trace_body_registry import (
    call_body_references,
    fail_registered_body,
    mark_call_body_failures,
    write_registered_body,
)


def test_writer_does_not_create_manifest_or_owner_for_unsaved_record() -> None:
    cursor = OwnerCursor()
    body = prepare_body("合成本文", part_characters=2)
    assert not write_registered_body(cursor, body, clob_type=object())
    assert not fail_registered_body(cursor, body.reference, "write_failed")
    assert not cursor.manifests and not cursor.owners and not cursor.parts


def test_registered_body_survives_one_owner_but_not_last_deletion() -> None:
    cursor = OwnerCursor()
    body = prepare_body("合成本文", part_characters=2)
    first, last = BodyOwner("rag", "message", "record-a"), BodyOwner("rag", "history", "record-b")
    attach_body_owner(cursor, first, [body.reference])
    attach_body_owner(cursor, last, [body.reference])
    release_body_owner(cursor, first)
    assert write_registered_body(cursor, body, clob_type=object())
    release_body_owner(cursor, last)
    assert not write_registered_body(cursor, body, clob_type=object())
    assert not fail_registered_body(cursor, body.reference, "write_failed")
    assert not cursor.parts.get(body.reference.body_id)


def test_reference_collection_ignores_model_payload_and_only_accepts_call_contract() -> None:
    reference = prepare_body("合成本文", part_characters=2).reference
    nested = {"body_reference": asdict(reference), "status": "recorded", "text": ""}
    assert call_body_references({"span-a": {"call": {"input": nested}}}) == (reference,)
    assert not call_body_references(
        {"span-a": {"call": {"input": {"text": str(nested), "status": "recorded"}}}}
    )
    assert not call_body_references(
        {"span-a": {"fields": [{"label": "result", "value": str(nested)}]}}
    )


def test_registration_failure_updates_only_matching_visible_call_snapshot() -> None:
    reference = prepare_body("合成本文", part_characters=2).reference
    source: dict[str, Any] = {
        "span-a": {
            "call": {
                "input": {
                    "status": "recorded",
                    "text": "preview",
                    "body_reference": asdict(reference),
                },
                "output": {"status": "recorded", "text": "retained-inline"},
                "schema": {"status": "withheld", "body_reference": asdict(reference)},
            }
        }
    }
    updated = mark_call_body_failures(source, {reference.body_id: "storage_unavailable"})
    assert updated["span-a"]["call"]["input"]["status"] == "incomplete"
    assert updated["span-a"]["call"]["input"]["failure_code"] == "storage_unavailable"
    assert updated["span-a"]["call"]["output"]["text"] == "retained-inline"
    assert updated["span-a"]["call"]["schema"]["status"] == "withheld"
    assert source["span-a"]["call"]["input"]["status"] == "recorded"


class UncommittedRegistrationCursor(OwnerCursor):
    """登録の取引が commit 前の間、manifest と所属を別の接続（writer）に見せない（#5394）。

    Oracle と同じく、同じ主キーの MERGE はその取引の終了を待ち、commit 済みなら ORA-00001 になる。
    """

    def __init__(self, registered: OwnerCursor, *, committed: bool) -> None:
        super().__init__()
        self.registered = registered
        self.committed = committed

    def execute(self, sql: str, binds: dict[str, Any] | None = None) -> None:
        if " ".join(sql.split()).startswith("MERGE INTO PLATFORM_TRACE_BODIES ") and self.committed:
            # 待っていた登録の取引が commit した。以後は commit 済みの行が見える。
            self.manifests = copy.deepcopy(self.registered.manifests)
            self.owners = set(self.registered.owners)
            raise RuntimeError(SimpleNamespace(code=1))
        super().execute(sql, binds)


def test_writer_waits_for_uncommitted_registration_instead_of_dropping_body() -> None:
    registration = OwnerCursor()
    body = prepare_body("合成本文" * 4, part_characters=2)
    attach_body_owner(registration, BodyOwner("rag", "message", "record-a"), [body.reference])
    writer = UncommittedRegistrationCursor(registration, committed=True)
    # commit 前の行は見えないが、主キーの競合で登録の取引の commit を待ってから書く。
    assert write_registered_body(writer, body, clob_type=object())
    assert writer.manifests[body.reference.body_id][5] == "complete"
    assert "".join(writer.parts[body.reference.body_id]) == "合成本文" * 4


def test_writer_leaves_no_manifest_when_registration_rolled_back() -> None:
    registration = OwnerCursor()
    body = prepare_body("合成本文", part_characters=2)
    attach_body_owner(registration, BodyOwner("rag", "message", "record-a"), [body.reference])
    # 登録の取引が rollback した: writer の MERGE は行を作れるが、所属が無いので戻して書かない。
    writer = UncommittedRegistrationCursor(registration, committed=False)
    assert not write_registered_body(writer, body, clob_type=object())
    assert not fail_registered_body(writer, body.reference, "write_failed")
    assert not writer.manifests and not writer.owners and not writer.parts
