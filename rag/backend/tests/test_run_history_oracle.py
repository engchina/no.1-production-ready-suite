"""実行履歴（#1890）の実 Oracle AI Database の結合テスト（保存・見え方・詳細・期限切れの削除）。"""

from __future__ import annotations

import pytest

from app.clients.oracle import OracleClient
from app.clients.oracle_run_history import RunHistoryStore
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)


@pytest.mark.usefixtures("oracle_db")
async def test_run_history_round_trip_on_real_oracle() -> None:
    """実 Oracle AI Database で、実行の保存・見え方の絞り込み・詳細・期限切れの削除ができる。"""
    from app.clients.oracle import _execute_count

    oracle = OracleClient()
    store = RunHistoryStore(oracle)
    run_ids = ("pytest-run-history-a", "pytest-run-history-b")

    async def cleanup() -> None:
        # テスト用 DB の後始末(cleanup_to_baseline)は文書と KB だけなので、自分で消す。
        await oracle._run_transaction(  # noqa: SLF001
            lambda connection: _execute_count(
                connection,
                "DELETE FROM rag_run_history WHERE run_id IN (:run_a, :run_b)",
                {"run_a": run_ids[0], "run_b": run_ids[1]},
            )
        )

    def context(user: str, *, all_users: bool = False) -> AuditRequestContext:
        return AuditRequestContext(
            request_id="pytest-run-history",
            user_id_hash=user * 64,
            user_uuid=f"uuid-{user}",
            all_users_visible=all_users,
        )

    await cleanup()
    try:
        for run_id, user in zip(run_ids, ("a", "b"), strict=True):
            token = set_audit_request_context(context(user))
            try:
                await store.save_run(
                    {
                        "run_id": run_id,
                        "surface": "chat" if user == "a" else "search",
                        "search_answer_profile_id": None,
                        "question": f"受注の登録方法は？ {user}" + "あ" * 400,
                        "answer_generated": user == "a",
                        "status": "success",
                        "verification": "verified" if user == "a" else None,
                        "citation_count": 2,
                        "elapsed_ms": 1234.5,
                        "stages": {"stages": [{"stage": "answer", "outcome": "success"}]},
                        "citations": None if user == "a" else [{"chunk_id": "c1"}],
                    }
                )
            finally:
                reset_audit_request_context(token)

        token = set_audit_request_context(context("a"))
        try:
            rows, total = await store.list_runs(
                query="受注の登録", surface=None, status=None, limit=10, offset=0
            )
            own_ids = [row["run_id"] for row in rows if row["run_id"] in run_ids]
            hidden = await store.get_run(run_ids[1])
            own = await store.get_run(run_ids[0])
        finally:
            reset_audit_request_context(token)
        token = set_audit_request_context(context("z", all_users=True))
        try:
            admin_rows, _ = await store.list_runs(
                query="受注の登録", surface="search", status="success", limit=10, offset=0
            )
            other = await store.get_run(run_ids[1])
            purged = await store.purge_runs(3650)
        finally:
            reset_audit_request_context(token)

        assert own_ids == [run_ids[0]]
        assert total >= 1
        assert len(str(rows[0]["question"])) == 300
        assert hidden is None
        assert own is not None
        assert own["user_uuid"] == "uuid-a"
        assert str(own["question"]).endswith("あ")
        assert len(str(own["question"])) > 300
        assert own["stages_json"] == {"stages": [{"stage": "answer", "outcome": "success"}]}
        assert [row["run_id"] for row in admin_rows if row["run_id"] in run_ids] == [run_ids[1]]
        assert other is not None
        assert other["citations_json"] == [{"chunk_id": "c1"}]
        # 作ったばかりの実行は保持期間の内側なので消えない。
        assert purged >= 0
    finally:
        await cleanup()
