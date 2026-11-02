"""抽出の修訂の履歴（#2644）の差分と API の判定（実 Oracle は別のファイル）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents as documents_route
from app.clients.oracle import DocumentReviewEditConflictError, ReviewedExtractionWrite
from app.rag.extraction_revisions import extraction_content_changed, extraction_element_changes
from app.schemas.document import ExtractionRevisionRestoreRequest, FileStatus

_DOCUMENT_ID = "doc-1"
_RECIPE_ID = "recipe-1"
_SHA = "a" * 64
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _payload(**texts: str) -> dict[str, object]:
    return {
        "raw_text": "\n".join(texts.values()),
        "elements": [
            {"element_id": key, "text": text, "page_number": 1} for key, text in texts.items()
        ],
    }


def test_element_changes_cover_changed_added_and_removed() -> None:
    changes = extraction_element_changes(_payload(a="A0", b="B0"), _payload(a="A1", c="C0"))

    assert [(c.element_id, c.change, c.before, c.after) for c in changes] == [
        ("a", "changed", "A0", "A1"),
        ("c", "added", None, "C0"),
        ("b", "removed", "B0", None),
    ]


def test_content_changed_ignores_inspection_index_and_structure() -> None:
    """保存のときに足す検査の索引などは内容の変更ではない(版を作らない)。"""
    before = _payload(a="A0")
    after = {**_payload(a="A0"), "parser_artifacts": {"inspection_index": {"version": 1}}}

    assert extraction_element_changes(before, after) == []
    assert extraction_content_changed(before, after) is False
    assert extraction_content_changed(before, _payload(a="A1")) is True
    # 旧クライアントの raw_text の修正は要素の文に現れない。
    assert extraction_content_changed(before, {**before, "raw_text": "edited"}) is True


def _revision(
    revision_no: int,
    *,
    kind: str = "EDITED",
    parent: int | None = None,
    base: int = 1,
    extraction_id: str | None = None,
    parse_id: str = "parse-now",
    extraction: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "revision_no": revision_no,
        "parent_revision_no": parent,
        "base_revision_no": base,
        "restored_from_revision_no": None,
        "revision_kind": kind,
        "extraction_recipe_id": extraction_id or f"er-{revision_no}",
        "parse_extraction_recipe_id": parse_id,
        "changed_element_count": 1,
        "created_at": _NOW,
        "extraction_json": extraction if extraction is not None else _payload(a=f"A{revision_no}"),
    }


class _Oracle:
    """履歴の API が読む分だけを持つ fake。"""

    def __init__(
        self, revisions: list[dict[str, object]], *, current: str, served: set[str] | None = None
    ) -> None:
        self.revisions = revisions
        self.current = current
        self.served = served or set()
        self.restore_calls: list[dict[str, Any]] = []
        self.restore_error: Exception | None = None

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        return {
            "recipe_id": recipe_id,
            "document_id": document_id,
            "slot_no": 1,
            "status": FileStatus.REVIEW.value,
            "processing_config": {},
            "active_extraction_recipe_id": self.current,
            "config_revision": 1,
            "created_at": _NOW,
            "updated_at": _NOW,
        }

    async def get_document_summary(self, document_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=document_id, content_sha256=_SHA)

    async def list_document_recipe_extraction_revisions(
        self, recipe_id: str
    ) -> list[dict[str, object]]:
        return self.revisions

    async def get_document_recipe_extraction_revision(
        self, recipe_id: str, revision_no: int
    ) -> dict[str, object] | None:
        return next((r for r in self.revisions if r["revision_no"] == revision_no), None)

    async def document_extraction_recipe_subset(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> None:
        return None

    async def recipe_served_extraction_ids(self, recipe_id: str) -> set[str]:
        return self.served

    async def restore_document_recipe_extraction_revision(self, **kwargs: Any) -> int:
        self.restore_calls.append(kwargs)
        if self.restore_error is not None:
            raise self.restore_error
        return 3


@pytest.fixture
def use_oracle(monkeypatch: pytest.MonkeyPatch) -> Callable[[_Oracle], _Oracle]:
    def install(fake: _Oracle) -> _Oracle:
        monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
        monkeypatch.setattr(
            documents_route, "_recipe_parse_extraction_id", lambda *_args: "parse-now"
        )

        async def recipe_view(_oracle: object, row: Mapping[str, object]) -> dict[str, object]:
            return dict(row)

        monkeypatch.setattr(documents_route, "_document_recipe_view", recipe_view)
        monkeypatch.setattr(documents_route, "enforce_rate_limit", lambda *_args: None)
        return fake

    return install


async def test_history_marks_current_serving_and_changed_base(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    use_oracle(
        _Oracle(
            [
                _revision(4, parent=3, base=3, extraction_id="er-now"),
                _revision(3, kind="PARSED", parent=2, base=3, extraction_id="er-parse-2"),
                _revision(2, parent=1, base=1, extraction_id="er-served", parse_id="parse-old"),
                _revision(1, kind="PARSED", base=1, extraction_id="er-parse-1"),
            ],
            current="er-now",
            served={"er-served"},
        )
    )

    history = (
        await documents_route.list_document_recipe_extraction_revisions(_DOCUMENT_ID, _RECIPE_ID)
    ).data

    assert history is not None
    assert history.current_revision_no == 4
    assert history.reparsed_since_last_revision is False
    by_no = {row.revision_no: row for row in history.revisions}
    assert (by_no[4].is_current, by_no[4].base_changed, by_no[4].kind) == (True, False, "edited")
    assert by_no[3].kind == "parsed" and by_no[3].base_changed is False
    assert (by_no[2].is_serving, by_no[2].base_changed, by_no[2].settings_changed) == (
        True,
        True,
        True,
    )
    assert by_no[1].base_changed is True and by_no[1].settings_changed is False


async def test_history_shows_reparse_without_rewriting_revisions(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    """解析をやり直した後は、今の抽出が版に無いことと、全ての版の基が変わったことを示す。"""
    use_oracle(_Oracle([_revision(2, parent=1, base=1)], current="er-reparsed"))

    history = (
        await documents_route.list_document_recipe_extraction_revisions(_DOCUMENT_ID, _RECIPE_ID)
    ).data

    assert history is not None
    assert history.current_revision_no is None
    assert history.reparsed_since_last_revision is True
    assert [row.base_changed for row in history.revisions] == [True]


async def test_history_without_revisions_is_not_a_reparse(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    use_oracle(_Oracle([], current="er-parsed"))

    history = (
        await documents_route.list_document_recipe_extraction_revisions(_DOCUMENT_ID, _RECIPE_ID)
    ).data

    assert history is not None
    assert history.revisions == []
    assert history.reparsed_since_last_revision is False


async def test_diff_defaults_to_parent_and_limits_long_text(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    long_text = "x" * 5000
    use_oracle(
        _Oracle(
            [
                _revision(2, parent=1, extraction=_payload(a=long_text)),
                _revision(1, kind="PARSED", extraction=_payload(a="A0")),
            ],
            current="er-2",
        )
    )

    diff = (
        await documents_route.diff_document_recipe_extraction_revision(_DOCUMENT_ID, _RECIPE_ID, 2)
    ).data

    assert diff is not None
    assert (diff.against_revision_no, diff.total) == (1, 1)
    assert diff.changes[0].before == "A0"
    assert diff.changes[0].after is not None and len(diff.changes[0].after) == 4000
    assert diff.changes[0].truncated is True


async def test_diff_of_pruned_parent_has_no_baseline(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    """親の版が上限で消えていれば比べる版なし。明示の比べる版が無ければ 404。"""
    use_oracle(_Oracle([_revision(5, parent=4)], current="er-5"))

    diff = (
        await documents_route.diff_document_recipe_extraction_revision(_DOCUMENT_ID, _RECIPE_ID, 5)
    ).data

    assert diff is not None
    assert (diff.against_revision_no, diff.total, diff.changes) == (None, 0, [])
    with pytest.raises(HTTPException) as missing:
        await documents_route.diff_document_recipe_extraction_revision(
            _DOCUMENT_ID, _RECIPE_ID, 5, against=4
        )
    assert missing.value.status_code == 404


async def _restore() -> Any:
    return await documents_route.restore_document_recipe_extraction_revision(
        SimpleNamespace(),  # type: ignore[arg-type]
        _DOCUMENT_ID,
        _RECIPE_ID,
        1,
        ExtractionRevisionRestoreRequest(expected_extraction_recipe_id="er-2"),
    )


async def test_restore_writes_revision_content_under_a_new_id(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    fake = use_oracle(_Oracle([_revision(1, kind="PARSED")], current="er-2"))

    await _restore()

    [call] = fake.restore_calls
    assert call["revision_no"] == 1
    assert call["expected_extraction_recipe_id"] == "er-2"
    build = call["build"]
    target = _revision(1, kind="PARSED")
    write: ReviewedExtractionWrite = build({}, {"status": "materialized"}, target)
    assert write.extraction == target["extraction_json"]
    assert write.extraction_recipe_id != build({}, {}, target).extraction_recipe_id
    assert write.parse_extraction_recipe_id == "parse-now"


async def test_restore_of_other_parse_settings_is_rejected(
    use_oracle: Callable[[_Oracle], _Oracle],
) -> None:
    """原本か解析の設定が今と違う版は復元しない(今の設定と内容が食い違うため)。"""
    fake = use_oracle(_Oracle([], current="er-2"))

    await _restore()

    with pytest.raises(DocumentReviewEditConflictError, match="設定が変わった"):
        fake.restore_calls[0]["build"]({}, {}, _revision(1, parse_id="parse-old"))


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (KeyError("抽出の版が見つかりません。"), 404),
        (DocumentReviewEditConflictError("確認待ちのレシピのみ修正できます。"), 409),
    ],
)
async def test_restore_errors_map_to_http(
    use_oracle: Callable[[_Oracle], _Oracle], error: Exception, status_code: int
) -> None:
    fake = use_oracle(_Oracle([], current="er-2"))
    fake.restore_error = error

    with pytest.raises(HTTPException) as exc_info:
        await _restore()

    assert exc_info.value.status_code == status_code
