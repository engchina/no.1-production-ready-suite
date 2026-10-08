"""業務ガイド（SupportGuide。#1237）の Oracle の保存。

ガイドの頭（`rag_support_guides`）に下書きと楽観ロックの版（`draft_revision`）を持ち、公開した版を
`rag_support_guide_revisions` に積む。公開・ロールバックは 1 つの transaction で版を足して頭の
`published_revision` を切り替える（失敗したら前の公開の版のまま）。回答は公開の版だけを使う。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

from app.clients.oracle import (
    OracleClient,
    OracleConnectionProtocol,
    _execute,
    _fetch_one,
    _json_bind,
    _json_input_sizes,
)
from app.rag.support_guide import content_sha256
from app.schemas.support_guide import (
    SupportGuideContent,
    SupportGuideRevision,
    SupportGuideRevisionSummary,
    SupportGuideSummary,
)

GUIDES_TABLE = "rag_support_guides"
REVISIONS_TABLE = "rag_support_guide_revisions"


class SupportGuideNotFoundError(LookupError):
    """ガイド（または版）が無い。"""


class SupportGuideConflictError(RuntimeError):
    """読み込んだ後にほかの人が下書きを保存した（楽観ロック）。"""

    def __init__(self, current_revision: int) -> None:
        super().__init__(current_revision)
        self.current_revision = current_revision


def oracle_support_guide_schema_sql() -> str:
    """業務ガイドの頭（下書き・公開の版の番号）の DDL（#1237）。

    検索・回答プロファイルは削除せずアーカイブするため、プロファイルへの FK は持たない
    （プロファイルの表の改名の migration と FK の修復に巻き込まない）。
    """
    return f"""
CREATE TABLE {GUIDES_TABLE} (
    guide_id                  VARCHAR2(64) PRIMARY KEY,
    search_answer_profile_id  VARCHAR2(64) NOT NULL,
    status                    VARCHAR2(16) DEFAULT 'active' NOT NULL,
    title                     VARCHAR2(400) NOT NULL,
    draft_json                JSON NOT NULL,
    draft_sha256              CHAR(64) NOT NULL,
    draft_revision            NUMBER(19) DEFAULT 1 NOT NULL,
    published_revision        NUMBER(19),
    published_sha256          CHAR(64),
    created_at                TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at                TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_by                VARCHAR2(256),
    CONSTRAINT {GUIDES_TABLE}_status_ck CHECK (status IN ('active', 'archived'))
);
CREATE INDEX {GUIDES_TABLE}_profile_idx ON {GUIDES_TABLE} (search_answer_profile_id, status);
""".strip()


def oracle_support_guide_revision_schema_sql() -> str:
    """業務ガイドの公開の版の DDL（#1237）。"""
    return f"""
CREATE TABLE {REVISIONS_TABLE} (
    guide_id        VARCHAR2(64) NOT NULL,
    revision        NUMBER(19) NOT NULL,
    title           VARCHAR2(400) NOT NULL,
    content_json    JSON NOT NULL,
    content_sha256  CHAR(64) NOT NULL,
    published_at    TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    published_by    VARCHAR2(256),
    rollback_from   NUMBER(19),
    CONSTRAINT {REVISIONS_TABLE}_pk PRIMARY KEY (guide_id, revision),
    CONSTRAINT {REVISIONS_TABLE}_guide_fk FOREIGN KEY (guide_id)
        REFERENCES {GUIDES_TABLE} (guide_id) ON DELETE CASCADE
);
""".strip()


def _int(value: object) -> int:
    return int(cast(Any, value))


def _json_value(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _summary(row: Mapping[str, Any]) -> SupportGuideSummary:
    published = row.get("published_revision")
    return SupportGuideSummary(
        guide_id=str(row["guide_id"]),
        search_answer_profile_id=str(row["search_answer_profile_id"]),
        status=cast(Any, str(row["status"])),
        title=str(row["title"]),
        draft_revision=int(row["draft_revision"]),
        published_revision=int(published) if published is not None else None,
        has_unpublished_changes=row.get("published_sha256") != row.get("draft_sha256"),
        updated_at=row["updated_at"],
        updated_by=row.get("updated_by"),
    )


def _revision_summary(row: Mapping[str, Any]) -> SupportGuideRevisionSummary:
    rollback = row.get("rollback_from")
    return SupportGuideRevisionSummary(
        revision=int(row["revision"]),
        title=str(row["title"]),
        content_sha256=str(row["content_sha256"]),
        published_at=row["published_at"],
        published_by=row.get("published_by"),
        rollback_from=int(rollback) if rollback is not None else None,
    )


_GUIDE_COLUMNS = """
    guide_id, search_answer_profile_id, status, title, draft_revision, published_revision,
    draft_sha256, published_sha256, updated_at, updated_by
"""


class SupportGuideStore:
    """業務ガイドの読み書き（OracleClient の接続と transaction を使う）。"""

    def __init__(self, oracle: OracleClient | None = None) -> None:
        self._oracle = oracle or OracleClient()

    async def list_guides(
        self, search_answer_profile_id: str, *, include_archived: bool = False
    ) -> list[SupportGuideSummary]:
        rows = await self._oracle._fetch_all(
            f"""
            SELECT {_GUIDE_COLUMNS}
            FROM {GUIDES_TABLE}
            WHERE search_answer_profile_id = :profile_id
              AND (:include_archived = 1 OR status = 'active')
            ORDER BY updated_at DESC, guide_id
            """,
            {"profile_id": search_answer_profile_id, "include_archived": int(include_archived)},
        )
        return [_summary(row) for row in rows]

    async def get_guide(
        self, search_answer_profile_id: str, guide_id: str
    ) -> tuple[SupportGuideSummary, SupportGuideContent]:
        row = await self._oracle._fetch_one(
            f"""
            SELECT {_GUIDE_COLUMNS}, draft_json
            FROM {GUIDES_TABLE}
            WHERE guide_id = :guide_id AND search_answer_profile_id = :profile_id
            """,
            {"guide_id": guide_id, "profile_id": search_answer_profile_id},
        )
        if row is None:
            raise SupportGuideNotFoundError(guide_id)
        return _summary(row), SupportGuideContent.model_validate(_json_value(row["draft_json"]))

    async def list_revisions(self, guide_id: str) -> list[SupportGuideRevisionSummary]:
        rows = await self._oracle._fetch_all(
            f"""
            SELECT revision, title, content_sha256, published_at, published_by, rollback_from
            FROM {REVISIONS_TABLE}
            WHERE guide_id = :guide_id
            ORDER BY revision DESC
            """,
            {"guide_id": guide_id},
        )
        return [_revision_summary(row) for row in rows]

    async def get_revision(self, guide_id: str, revision: int) -> SupportGuideRevision:
        row = await self._oracle._fetch_one(
            f"""
            SELECT revision, title, content_sha256, published_at, published_by, rollback_from,
                   content_json
            FROM {REVISIONS_TABLE}
            WHERE guide_id = :guide_id AND revision = :revision
            """,
            {"guide_id": guide_id, "revision": revision},
        )
        if row is None:
            raise SupportGuideNotFoundError(f"{guide_id}@{revision}")
        summary = _revision_summary(row)
        return SupportGuideRevision(
            **summary.model_dump(),
            content=SupportGuideContent.model_validate(_json_value(row["content_json"])),
        )

    async def published_contents(
        self, search_answer_profile_id: str
    ) -> list[tuple[str, int, SupportGuideContent]]:
        """回答に使う公開の版（アーカイブしていないガイドだけ）。"""
        rows = await self._oracle._fetch_all(
            f"""
            SELECT g.guide_id, r.revision, r.content_json
            FROM {GUIDES_TABLE} g
            JOIN {REVISIONS_TABLE} r
              ON r.guide_id = g.guide_id AND r.revision = g.published_revision
            WHERE g.search_answer_profile_id = :profile_id AND g.status = 'active'
            ORDER BY g.guide_id
            """,
            {"profile_id": search_answer_profile_id},
        )
        return [
            (
                str(row["guide_id"]),
                _int(row["revision"]),
                SupportGuideContent.model_validate(_json_value(row["content_json"])),
            )
            for row in rows
        ]

    async def create_guide(
        self, search_answer_profile_id: str, content: SupportGuideContent, *, user: str | None
    ) -> str:
        guide_id = uuid.uuid4().hex
        binds = {
            "guide_id": guide_id,
            "profile_id": search_answer_profile_id,
            "title": content.title,
            "draft_json": _json_bind(content.model_dump(mode="json")),
            "draft_sha256": content_sha256(content),
            "updated_by": user,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                f"""
                INSERT INTO {GUIDES_TABLE} (
                    guide_id, search_answer_profile_id, title, draft_json, draft_sha256, updated_by
                ) VALUES (
                    :guide_id, :profile_id, :title, :draft_json, :draft_sha256, :updated_by
                )
                """,
                binds,
                input_sizes=_json_input_sizes("draft_json"),
            )

        await self._oracle._run_transaction(operation)
        return guide_id

    async def save_draft(
        self,
        search_answer_profile_id: str,
        guide_id: str,
        content: SupportGuideContent,
        *,
        base_revision: int,
        user: str | None,
    ) -> int:
        """下書きを保存して新しい draft_revision を返す。版が違えば SupportGuideConflictError。"""
        binds = {
            "guide_id": guide_id,
            "title": content.title,
            "draft_json": _json_bind(content.model_dump(mode="json")),
            "draft_sha256": content_sha256(content),
            "updated_by": user,
        }

        def operation(connection: OracleConnectionProtocol) -> int:
            current = self._lock(connection, search_answer_profile_id, guide_id)
            if current != base_revision:
                raise SupportGuideConflictError(current)
            _execute(
                connection,
                f"""
                UPDATE {GUIDES_TABLE}
                SET title = :title, draft_json = :draft_json, draft_sha256 = :draft_sha256,
                    draft_revision = draft_revision + 1, updated_at = SYSTIMESTAMP,
                    updated_by = :updated_by
                WHERE guide_id = :guide_id
                """,
                binds,
                input_sizes=_json_input_sizes("draft_json"),
            )
            return current + 1

        return int(await self._oracle._run_transaction(operation))

    async def publish(
        self,
        search_answer_profile_id: str,
        guide_id: str,
        content: SupportGuideContent,
        *,
        base_revision: int,
        user: str | None,
        rollback_from: int | None = None,
    ) -> int:
        """内容を新しい公開の版にし、その版の番号を返す。

        ``base_revision``（読み込んだ下書きの版）を照合し、違えば ``SupportGuideConflictError``。
        ロールバックは古い版の内容を新しい版として公開し、下書きもその内容に置き換える（ほかの人の
        保存した下書きを黙って失わないよう、ロールバックも照合する。#1278）。
        """
        sha = content_sha256(content)
        content_json = _json_bind(content.model_dump(mode="json"))

        def operation(connection: OracleConnectionProtocol) -> int:
            current = self._lock(connection, search_answer_profile_id, guide_id)
            if current != base_revision:
                raise SupportGuideConflictError(current)
            row = _fetch_one(
                connection,
                f"SELECT NVL(MAX(revision), 0) AS latest FROM {REVISIONS_TABLE} "
                "WHERE guide_id = :guide_id",
                {"guide_id": guide_id},
            )
            revision = _int((row or {}).get("latest") or 0) + 1
            _execute(
                connection,
                f"""
                INSERT INTO {REVISIONS_TABLE} (
                    guide_id, revision, title, content_json, content_sha256, published_by,
                    rollback_from
                ) VALUES (
                    :guide_id, :revision, :title, :content_json, :content_sha256, :published_by,
                    :rollback_from
                )
                """,
                {
                    "guide_id": guide_id,
                    "revision": revision,
                    "title": content.title,
                    "content_json": content_json,
                    "content_sha256": sha,
                    "published_by": user,
                    "rollback_from": rollback_from,
                },
                input_sizes=_json_input_sizes("content_json"),
            )
            draft_update = (
                ", draft_json = :draft_json, draft_sha256 = :draft_sha256, title = :title, "
                "draft_revision = draft_revision + 1"
                if rollback_from is not None
                else ""
            )
            binds: dict[str, object] = {
                "guide_id": guide_id,
                "revision": revision,
                "sha": sha,
                "updated_by": user,
            }
            if rollback_from is not None:
                binds.update(draft_json=content_json, draft_sha256=sha, title=content.title)
            _execute(
                connection,
                f"""
                UPDATE {GUIDES_TABLE}
                SET published_revision = :revision, published_sha256 = :sha,
                    updated_at = SYSTIMESTAMP, updated_by = :updated_by{draft_update}
                WHERE guide_id = :guide_id
                """,
                binds,
                input_sizes=_json_input_sizes("draft_json") if rollback_from is not None else None,
            )
            return revision

        return int(await self._oracle._run_transaction(operation))

    async def set_status(
        self, search_answer_profile_id: str, guide_id: str, status: str, *, user: str | None
    ) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            self._lock(connection, search_answer_profile_id, guide_id)
            _execute(
                connection,
                f"""
                UPDATE {GUIDES_TABLE}
                SET status = :status, updated_at = SYSTIMESTAMP, updated_by = :updated_by
                WHERE guide_id = :guide_id
                """,
                {"guide_id": guide_id, "status": status, "updated_by": user},
            )

        await self._oracle._run_transaction(operation)

    @staticmethod
    def _lock(
        connection: OracleConnectionProtocol, search_answer_profile_id: str, guide_id: str
    ) -> int:
        row = _fetch_one(
            connection,
            f"""
            SELECT draft_revision FROM {GUIDES_TABLE}
            WHERE guide_id = :guide_id AND search_answer_profile_id = :profile_id
            FOR UPDATE
            """,
            {"guide_id": guide_id, "profile_id": search_answer_profile_id},
        )
        if row is None:
            raise SupportGuideNotFoundError(guide_id)
        return _int(row["draft_revision"])


__all__ = [
    "GUIDES_TABLE",
    "REVISIONS_TABLE",
    "SupportGuideConflictError",
    "SupportGuideNotFoundError",
    "SupportGuideStore",
    "oracle_support_guide_revision_schema_sql",
    "oracle_support_guide_schema_sql",
]
