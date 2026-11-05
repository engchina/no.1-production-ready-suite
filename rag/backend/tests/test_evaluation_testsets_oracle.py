"""実 Oracle で下書きの永続化・本人分離・競合更新を確認する（合成データ）。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.evaluation_testset_store import OracleTestSetStore
from app.clients.oracle import OracleClient
from app.rag.evaluation_testsets import edited_draft, new_draft
from app.rag.request_context import (
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.evaluation_testsets import TestSetFields as DraftFields
from app.schemas.evaluation_testsets import TestSetItem as DraftItem
from app.schemas.evaluation_testsets import TestSetSaveRequest as SaveRequest
from tests import _oracle_test_db


@pytest.mark.asyncio
async def test_oracle_draft_survives_store_recreation_and_denies_other_owner(
    oracle_db: object,
) -> None:
    context = current_audit_request_context()
    owner = uuid4().hex
    token = set_audit_request_context(replace(context, user_id_hash=owner))
    fields = DraftFields(question="合成の質問", standard_answer="合成の標準回答")
    entry = DraftItem(
        **fields.model_dump(),
        item_id="item-a",
        source_job_id="job-a",
        source_case_id="row-2",
        original=fields,
    )
    draft = new_draft("合成の下書き", [entry])
    try:
        store = OracleTestSetStore(OracleClient())
        await store.create(draft)
        reopened = await OracleTestSetStore(OracleClient()).get(draft.draft_id)
        assert reopened == draft
        assert any(value.draft_id == draft.draft_id for value in await store.list())
        updated = edited_draft(
            draft,
            SaveRequest(
                name="合成の変更",
                revision=1,
                items=[
                    {
                        **fields.model_dump(),
                        "item_id": entry.item_id,
                        "confirmed": True,
                    }
                ],
            ),
        )
        assert await store.save(updated, 1)
        assert not await store.save(updated, 1)
        latest = await store.get(draft.draft_id)
        assert latest is not None and latest.items[0].confirmed
        other_token = set_audit_request_context(
            replace(context, user_id_hash=uuid4().hex, all_users_visible=True)
        )
        try:
            assert await store.get(draft.draft_id) is None
            assert all(value.draft_id != draft.draft_id for value in await store.list())
            assert not await store.save(updated, 2)
        finally:
            reset_audit_request_context(other_token)
        tenant_token = set_audit_request_context(
            replace(context, user_id_hash=owner, tenant_id_hash=uuid4().hex)
        )
        try:
            assert await store.get(draft.draft_id) is None
        finally:
            reset_audit_request_context(tenant_token)
    finally:
        reset_audit_request_context(token)
        with _oracle_test_db._schema_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM rag_evaluation_testsets WHERE draft_id = :id", {"id": draft.draft_id}
            )
            connection.commit()
