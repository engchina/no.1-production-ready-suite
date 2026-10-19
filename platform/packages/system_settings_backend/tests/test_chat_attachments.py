"""会話の添付（#2517）: 種類の判定・解析・上限・期限・見え方・ツール・API・Oracle の SQL。"""

from __future__ import annotations

import asyncio
import io
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from chat_attachment_fixtures import PNG_BYTES, docx_bytes, pdf_bytes, pptx_bytes, xlsx_bytes
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.migrations import PLATFORM_AUTH_DDL
from pr_system_settings.auth.store import PLATFORM_AUTH_TABLES
from pr_system_settings.chat_attachments import (
    CHAT_ATTACHMENT_TOOL_NAMES,
    CHAT_ATTACHMENT_TOOL_SPECS,
    CHAT_ATTACHMENTS_TABLE,
    ChatAttachmentError,
    ChatAttachmentService,
    ChatAttachmentToolError,
    InMemoryChatAttachmentStore,
    OracleChatAttachmentStore,
    build_chat_attachment_router,
    chat_attachment_limits,
)
from pr_system_settings.chat_attachments.limits import (
    CHAT_ATTACHMENT_DEFAULT_MAX_BYTES,
    MAX_ZIP_UNCOMPRESSED_BYTES,
)
from pr_system_settings.chat_attachments.parsing import (
    WARNING_MACROS_NOT_EXECUTED,
    WARNING_PDF_PAGE_WITHOUT_TEXT,
    detect_format,
    parse_attachment,
    parse_document,
)


@dataclass(frozen=True)
class _Principal:
    user_uuid: str
    is_system_admin: bool = False


ALICE = _Principal("user-a")
BOB = _Principal("user-b")
ADMIN = _Principal("admin-1", is_system_admin=True)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _service(
    *,
    settings: object | None = None,
    image_text: str | None = None,
    clock: _Clock | None = None,
    product: Any = "agent",
    store: InMemoryChatAttachmentStore | None = None,
) -> ChatAttachmentService:
    async def reader(data: bytes, media_type: str) -> str:
        assert media_type == "image/png"
        return image_text or ""

    return ChatAttachmentService(
        store or InMemoryChatAttachmentStore(),
        product=product,
        settings=lambda: settings or SimpleNamespace(),
        image_reader=lambda: reader if image_text is not None else None,
        clock=clock or _Clock(),
    )


def _upload(
    service: ChatAttachmentService, principal: _Principal, name: str, data: bytes, **kwargs: Any
) -> Any:
    return asyncio.run(service.upload(principal, file_name=name, data=data, **kwargs))


# ---- 種類の判定 ----


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("a.exe", b"MZ", "CHAT_ATTACHMENT_UNSUPPORTED_TYPE"),
        ("a.doc", b"\xd0\xcf\x11\xe0", "CHAT_ATTACHMENT_UNSUPPORTED_TYPE"),
        ("a.pdf", b"PK\x03\x04", "CHAT_ATTACHMENT_TYPE_MISMATCH"),
        ("a.docx", b"%PDF-1.4", "CHAT_ATTACHMENT_TYPE_MISMATCH"),
        ("a.png", b"\xff\xd8\xff", "CHAT_ATTACHMENT_TYPE_MISMATCH"),
        ("a.txt", b"PK\x03\x04abc", "CHAT_ATTACHMENT_TYPE_MISMATCH"),
        ("a.txt", b"bin\x00ary", "CHAT_ATTACHMENT_TYPE_MISMATCH"),
        ("a.txt", b"", "CHAT_ATTACHMENT_UNREADABLE"),
    ],
    ids=["exe", "legacy-doc", "pdf-zip", "docx-pdf", "png-jpeg", "txt-zip", "txt-nul", "empty"],
)
def test_detect_format_rejects(name: str, data: bytes, code: str) -> None:
    with pytest.raises(ChatAttachmentError) as caught:
        detect_format(name, data)
    assert caught.value.code == code
    assert caught.value.status_code in {415, 422}


def test_detect_format_accepts_by_extension_and_magic() -> None:
    assert detect_format("x.PDF", pdf_bytes(["Hi"])).kind == "pdf"
    assert detect_format("x.xlsm", xlsx_bytes()).kind == "excel"
    assert detect_format("x.png", PNG_BYTES).media_type == "image/png"


# ---- 解析 ----


def test_parse_pdf_one_section_per_page_and_warns_empty_page() -> None:
    parsed = parse_document("a.pdf", pdf_bytes(["First page text", "", "Third page"]), "pdf")
    assert [(s.section_id, s.title, s.page) for s in parsed.sections] == [
        ("s1", "p.1", 1),
        ("s2", "p.3", 3),
    ]
    assert "First page text" in parsed.sections[0].text
    assert WARNING_PDF_PAGE_WITHOUT_TEXT in parsed.warnings


def test_parse_pdf_without_text_is_unreadable() -> None:
    with pytest.raises(ChatAttachmentError) as caught:
        parse_document("a.pdf", pdf_bytes([""]), "pdf")
    assert caught.value.status_code == 422


def test_parse_docx_splits_by_heading_and_flattens_tables() -> None:
    parsed = parse_document("a.docx", docx_bytes(), "word")
    assert [(s.title, s.level) for s in parsed.sections] == [
        ("a.docx", 1),
        ("第一の見出し", 1),
        ("第二の見出し", 2),
    ]
    assert "項目: 甲 / 値: 10" in parsed.sections[1].text
    assert parsed.warnings == []


def test_parse_docm_reads_text_without_macros() -> None:
    parsed = parse_document("a.docm", docx_bytes(macro=True), "word")
    assert WARNING_MACROS_NOT_EXECUTED in parsed.warnings
    assert "見出しの下の本文 B" in parsed.sections[-1].text


def test_parse_pptx_follows_slide_order_and_reads_notes() -> None:
    parsed = parse_document("a.pptx", pptx_bytes(), "powerpoint")
    assert [s.title for s in parsed.sections] == [
        "スライド 1: 最初のスライド",
        "スライド 2: 次のスライド",
    ]
    assert "話す内容のメモ" in parsed.sections[0].text
    assert "99" not in parsed.sections[0].text


def test_parse_xlsx_rows_with_column_names() -> None:
    parsed = parse_document("a.xlsm", xlsx_bytes(macro=True), "excel")
    assert WARNING_MACROS_NOT_EXECUTED in parsed.warnings
    assert len(parsed.sheets) == 1
    sheet = parsed.sheets[0]
    assert sheet.sheet == "一覧"
    assert sheet.columns == ["コード", "名前", "金額"]
    assert sheet.rows == [["A-1", "品目甲", 1200], ["A-2", "品目乙", 3400.5]]


def test_parse_csv_names_blank_and_duplicate_columns() -> None:
    data = "id,,id\n1,x,2\n\n3,y,4\n".encode("cp932")
    parsed = parse_document("rows.csv", data, "csv")
    assert parsed.sheets[0].sheet == "rows"
    assert parsed.sheets[0].columns == ["id", "列2", "id_2"]
    assert parsed.sheets[0].rows == [["1", "x", "2"], ["3", "y", "4"]]


def test_parse_text_by_markdown_headings_and_sjis() -> None:
    parsed = parse_document("a.md", "前書き\n# 章 1\n本文 1\n## 節 1.1\n本文 2\n".encode(), "text")
    assert [(s.title, s.level) for s in parsed.sections] == [
        ("a.md", 1),
        ("章 1", 1),
        ("節 1.1", 2),
    ]
    sjis = parse_document("b.txt", ("日本語の文章です。" * 40).encode("cp932"), "text")
    assert sjis.sections[0].text.startswith("日本語の文章です。")


def test_zip_bomb_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        info = zipfile.ZipInfo("word/big.bin")
        archive.writestr(info, b"\x00" * (MAX_ZIP_UNCOMPRESSED_BYTES + 1))
    with pytest.raises(ChatAttachmentError) as caught:
        parse_document("a.docx", buffer.getvalue(), "word")
    assert caught.value.status_code == 422


def test_xml_entities_are_not_expanded() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "boom">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", evil)
    with pytest.raises(ChatAttachmentError):
        parse_document("a.docx", buffer.getvalue(), "word")


def test_image_uses_reader_and_requires_vision_model() -> None:
    async def reader(data: bytes, media_type: str) -> str:
        return "画面に表が表示されている"

    spec, parsed = asyncio.run(parse_attachment("a.png", PNG_BYTES, image_reader=reader))
    assert spec.kind == "image"
    assert parsed.sections[0].text == "画面に表が表示されている"
    with pytest.raises(ChatAttachmentError) as caught:
        asyncio.run(parse_attachment("a.png", PNG_BYTES, image_reader=None))
    assert caught.value.code == "CHAT_ATTACHMENT_IMAGE_MODEL_MISSING"


# ---- 上限・期限・見え方 ----


def test_limits_clamp_settings() -> None:
    assert chat_attachment_limits(SimpleNamespace()).max_bytes == CHAT_ATTACHMENT_DEFAULT_MAX_BYTES
    limits = chat_attachment_limits(
        SimpleNamespace(
            chat_attachment_ttl_hours=0,
            chat_attachment_max_bytes=10**12,
            chat_attachment_max_files="x",
        )
    )
    assert (limits.ttl_hours, limits.max_bytes, limits.max_files) == (1, 100 * 1024 * 1024, 10)


def test_upload_rejects_too_large_and_too_many() -> None:
    service = _service(
        settings=SimpleNamespace(chat_attachment_max_bytes=1024 * 1024, chat_attachment_max_files=2)
    )
    with pytest.raises(ChatAttachmentError) as caught:
        _upload(service, ALICE, "a.txt", b"x" * (1024 * 1024 + 1))
    assert caught.value.status_code == 413
    _upload(service, ALICE, "a.txt", b"one")
    _upload(service, ALICE, "b.txt", b"two")
    with pytest.raises(ChatAttachmentError) as caught:
        _upload(service, ALICE, "c.txt", b"three")
    assert caught.value.code == "CHAT_ATTACHMENT_TOO_MANY"
    # 他の利用者の送る前の添付は数えない。
    _upload(service, BOB, "d.txt", b"four")


def test_pending_attachment_is_visible_to_owner_and_admin_only() -> None:
    service = _service()
    item = _upload(service, ALICE, "../dir/a.txt", b"hello")
    assert item.file_name == "a.txt"
    assert [i.attachment_id for i in service.list_items(ALICE).items] == [item.attachment_id]
    assert service.list_items(BOB).items == []
    with pytest.raises(ChatAttachmentError) as caught:
        service.delete(BOB, item.attachment_id)
    assert caught.value.status_code == 404
    service.delete(ADMIN, item.attachment_id)
    assert service.list_items(ALICE).items == []


def test_attach_binds_to_conversation_and_checks_other_conversation() -> None:
    service = _service()
    first = _upload(service, ALICE, "a.txt", b"hello")
    refs = service.attach_to_conversation(
        ALICE, [first.attachment_id], conversation_id="c1", owner_user_uuid=ALICE.user_uuid
    )
    assert [ref.file_name for ref in refs] == ["a.txt"]
    assert service.list_items(ALICE).items == []
    assert [i.attachment_id for i in service.list_items(ALICE, conversation_id="c1").items] == [
        first.attachment_id
    ]
    with pytest.raises(ChatAttachmentError) as caught:
        service.attach_to_conversation(
            ALICE, [first.attachment_id], conversation_id="c2", owner_user_uuid=ALICE.user_uuid
        )
    assert caught.value.code == "CHAT_ATTACHMENT_OTHER_CONVERSATION"
    with pytest.raises(ChatAttachmentError) as caught:
        service.attach_to_conversation(
            BOB, [first.attachment_id], conversation_id="c1", owner_user_uuid=ALICE.user_uuid
        )
    assert caught.value.status_code == 404


def test_admin_upload_into_other_users_conversation_keeps_owner() -> None:
    service = _service()
    item = _upload(
        service,
        ADMIN,
        "a.txt",
        b"hello",
        conversation_id="c1",
        conversation_owner_user_uuid=ALICE.user_uuid,
    )
    assert item.uploaded_by_me
    record = service.store.get(item.attachment_id)
    assert record is not None
    assert (record.owner_user_uuid, record.uploaded_by_user_uuid) == (
        ALICE.user_uuid,
        ADMIN.user_uuid,
    )
    listed = service.list_items(ALICE, conversation_id="c1").items
    assert [i.uploaded_by_me for i in listed] == [False]
    service.delete(ALICE, item.attachment_id)


def test_expired_attachments_disappear_and_use_extends_expiry() -> None:
    clock = _Clock()
    store = InMemoryChatAttachmentStore()
    service = _service(
        settings=SimpleNamespace(chat_attachment_ttl_hours=2), clock=clock, store=store
    )
    kept = _upload(service, ALICE, "a.txt", b"keep")
    dropped = _upload(service, ALICE, "b.txt", b"drop")
    clock.now += timedelta(hours=1)
    service.attach_to_conversation(
        ALICE, [kept.attachment_id], conversation_id="c1", owner_user_uuid=ALICE.user_uuid
    )
    clock.now += timedelta(hours=1, minutes=30)
    assert [r.attachment_id for r in service.conversation_records("c1")] == [kept.attachment_id]
    assert service.list_items(ALICE).items == []
    assert store.get(dropped.attachment_id) is None  # 消えている
    with pytest.raises(ChatAttachmentError):
        service.attach_to_conversation(
            ALICE, [dropped.attachment_id], conversation_id="c1", owner_user_uuid=ALICE.user_uuid
        )
    clock.now += timedelta(hours=2)
    assert service.conversation_records("c1") == []


def test_products_do_not_share_attachments() -> None:
    store = InMemoryChatAttachmentStore()
    agent = _service(store=store, product="agent")
    rag = _service(store=store, product="rag")
    item = _upload(agent, ALICE, "a.txt", b"hello")
    assert rag.list_items(ALICE).items == []
    with pytest.raises(ChatAttachmentError):
        rag.delete(ALICE, item.attachment_id)


def test_delete_conversation_removes_its_attachments() -> None:
    service = _service()
    item = _upload(
        service,
        ALICE,
        "a.txt",
        b"hello",
        conversation_id="c1",
        conversation_owner_user_uuid="user-a",
    )
    assert service.delete_conversation("c1") == 1
    assert service.store.get(item.attachment_id) is None


# ---- ツール ----


def _toolset() -> Any:
    service = _service()
    doc = _upload(
        service,
        ALICE,
        "a.docx",
        docx_bytes(),
        conversation_id="c1",
        conversation_owner_user_uuid="user-a",
    )
    sheet = _upload(
        service,
        ALICE,
        "b.xlsx",
        xlsx_bytes(),
        conversation_id="c1",
        conversation_owner_user_uuid="user-a",
    )
    return service.toolset("c1"), doc.attachment_id, sheet.attachment_id


def test_tool_specs_cover_the_fixed_names() -> None:
    assert tuple(spec.name for spec in CHAT_ATTACHMENT_TOOL_SPECS) == CHAT_ATTACHMENT_TOOL_NAMES
    for spec in CHAT_ATTACHMENT_TOOL_SPECS:
        schema = spec.input_schema
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_list_tool_returns_outline_without_bodies() -> None:
    toolset, doc_id, sheet_id = _toolset()
    result = toolset.call("chat_attachment_list", {})
    by_id = {item["attachment_id"]: item for item in result["attachments"]}
    assert by_id[doc_id]["kind"] == "word"
    assert by_id[doc_id]["sections"][1] == {
        "section_id": "s2",
        "title": "第一の見出し",
        "level": 1,
        "page": None,
        "characters": len("第一の見出し\n見出しの下の本文 A\n項目: 甲 / 値: 10"),
    }
    assert by_id[sheet_id]["sheets"] == [
        {"sheet": "一覧", "columns": ["コード", "名前", "金額"], "row_count": 2}
    ]
    assert "text" not in str(result)
    assert (
        f"- b.xlsx（attachment_id={sheet_id}、種類=excel、シート「一覧」2 行）"
        in toolset.index_lines()
    )


def test_read_section_pages_through_text() -> None:
    toolset, doc_id, _ = _toolset()
    result = toolset.call(
        "chat_attachment_read_section", {"attachment_id": doc_id, "section_id": "s2", "offset": 0}
    )
    assert result["title"] == "第一の見出し"
    assert result["next_offset"] is None
    tail = toolset.call(
        "chat_attachment_read_section", {"attachment_id": doc_id, "section_id": "s2", "offset": 7}
    )
    assert tail["content"].startswith("見出しの下")


def test_read_rows_returns_column_value_pairs() -> None:
    toolset, _, sheet_id = _toolset()
    result = toolset.call(
        "chat_attachment_read_rows",
        {"attachment_id": sheet_id, "sheet": "一覧", "start_row": 2, "limit": 5},
    )
    assert result["rows"] == [
        {"row_number": 2, "values": {"コード": "A-2", "名前": "品目乙", "金額": 3400.5}}
    ]
    assert (result["row_count"], result["next_start_row"]) == (2, None)
    first = toolset.call(
        "chat_attachment_read_rows",
        {"attachment_id": sheet_id, "sheet": "一覧", "start_row": 1, "limit": 1},
    )
    assert first["next_start_row"] == 2


def test_find_matches_sections_and_rows_case_and_width_insensitive() -> None:
    toolset, doc_id, sheet_id = _toolset()
    result = toolset.call(
        "chat_attachment_find", {"text": "ａ－２", "attachment_id": None, "limit": 5}
    )
    assert result["matches"] == [
        {
            "attachment_id": sheet_id,
            "file_name": "b.xlsx",
            "section_id": None,
            "sheet": "一覧",
            "row_number": 2,
            "snippet": "コード: A-2 / 名前: 品目乙 / 金額: 3400.5",
        }
    ]
    body = toolset.call(
        "chat_attachment_find", {"text": "本文", "attachment_id": doc_id, "limit": 1}
    )
    assert body["truncated"] is True and len(body["matches"]) == 1


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        (
            "chat_attachment_read_section",
            {"attachment_id": "x", "section_id": "s1", "offset": 0},
            "この会話に無い",
        ),
        (
            "chat_attachment_read_section",
            {"attachment_id": "DOC", "section_id": "s9", "offset": 0},
            "section_id",
        ),
        (
            "chat_attachment_read_section",
            {"attachment_id": "DOC", "section_id": "s1", "offset": -1},
            "offset",
        ),
        (
            "chat_attachment_read_rows",
            {"attachment_id": "DOC", "sheet": "一覧", "start_row": 1, "limit": 1},
            "表（Excel・CSV）ではありません",
        ),
        (
            "chat_attachment_read_rows",
            {"attachment_id": "SHEET", "sheet": "無い", "start_row": 1, "limit": 1},
            "シート: 一覧",
        ),
        (
            "chat_attachment_read_rows",
            {"attachment_id": "SHEET", "sheet": "一覧", "start_row": 1, "limit": 500},
            "limit",
        ),
        ("chat_attachment_unknown", {}, "添付のツールではありません"),
    ],
    ids=[
        "unknown-id",
        "unknown-section",
        "negative-offset",
        "not-table",
        "unknown-sheet",
        "limit",
        "unknown-tool",
    ],
)
def test_tool_argument_errors_explain_fix(
    name: str, arguments: dict[str, Any], message: str
) -> None:
    toolset, doc_id, sheet_id = _toolset()
    replaced = {
        key: (doc_id if value == "DOC" else sheet_id if value == "SHEET" else value)
        for key, value in arguments.items()
    }
    with pytest.raises(ChatAttachmentToolError, match=message):
        toolset.call(name, replaced)


# ---- API ----


def _client(service: ChatAttachmentService, principal: _Principal) -> TestClient:
    app = FastAPI()

    @app.exception_handler(SecurityApiError)
    async def handle(_: Request, exc: SecurityApiError) -> JSONResponse:
        return JSONResponse(
            {"code": exc.code, "message": exc.public_message}, status_code=exc.status_code
        )

    def owner(current: _Principal, conversation_id: str) -> str:
        if conversation_id == "hidden":
            raise SecurityApiError(404, "会話が見つかりません。")
        return "user-a"

    app.include_router(
        build_chat_attachment_router(
            get_service=lambda: service,
            current_principal=lambda: principal,
            conversation_owner=owner,
        ),
        prefix="/api",
    )
    return TestClient(app)


def test_api_upload_list_delete() -> None:
    service = _service(settings=SimpleNamespace(chat_attachment_max_bytes=1024 * 1024))
    client = _client(service, ALICE)
    response = client.post(
        "/api/chat-attachments",
        files={"file": ("a.csv", b"col\n1\n", "text/csv")},
        data={"conversation_id": "c1"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert (item["kind"], item["conversation_id"], item["sheets"][0]["row_count"]) == (
        "csv",
        "c1",
        1,
    )
    listed = client.get("/api/chat-attachments", params={"conversation_id": "c1"}).json()["data"]
    assert [i["attachment_id"] for i in listed["items"]] == [item["attachment_id"]]
    assert listed["limits"]["max_bytes"] == 1024 * 1024
    assert ".png" not in listed["limits"]["accept"] and listed["limits"]["image_enabled"] is False
    assert client.delete(f"/api/chat-attachments/{item['attachment_id']}").json()["data"] == {
        "deleted": True
    }
    assert client.delete(f"/api/chat-attachments/{item['attachment_id']}").status_code == 404


def test_api_rejects_hidden_conversation_and_large_file() -> None:
    service = _service(settings=SimpleNamespace(chat_attachment_max_bytes=1024 * 1024))
    client = _client(service, ALICE)
    hidden = client.post(
        "/api/chat-attachments",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"conversation_id": "hidden"},
    )
    assert hidden.status_code == 404
    large = client.post(
        "/api/chat-attachments", files={"file": ("a.txt", b"x" * (1024 * 1024 + 1), "text/plain")}
    )
    assert large.status_code == 413
    assert large.json()["code"] == "CHAT_ATTACHMENT_TOO_LARGE"


# ---- Oracle ----


class _Cursor:
    def __init__(self, log: list[tuple[str, Any]], rows: list[Any], fail: Exception | None) -> None:
        self._log = log
        self._rows = rows
        self._fail = fail
        self.rowcount = 1

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, binds: Any = None) -> None:
        if self._fail is not None:
            raise self._fail
        self._log.append((" ".join(sql.split()), binds))

    def executemany(self, sql: str, binds: Any) -> None:
        self._log.append((" ".join(sql.split()), binds))

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


def _oracle(
    rows: list[Any] | None = None, fail: Exception | None = None
) -> tuple[OracleChatAttachmentStore, list[tuple[str, Any]]]:
    log: list[tuple[str, Any]] = []

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor(log, rows or [], fail)

        def commit(self) -> None:
            log.append(("COMMIT", None))

    @contextmanager
    def factory() -> Any:
        yield _Connection()

    return OracleChatAttachmentStore(factory), log


def test_oracle_store_lists_by_conversation_and_reads_outline() -> None:
    created = datetime(2026, 10, 19, 9, 0)
    row = (
        "id-1",
        "agent",
        "user-a",
        "user-a",
        "c1",
        "a.csv",
        "text/csv",
        "csv",
        6,
        "0" * 64,
        '{"sections": [], "sheets": [{"sheet": "a", "columns": ["col"], "row_count": 1}], '
        '"warnings": []}',
        created,
        created + timedelta(hours=24),
    )
    store, log = _oracle(rows=[row])
    records = store.list_for_conversation("agent", "c1", now=datetime.now(UTC))
    assert records[0].outline.sheets[0].row_count == 1
    assert records[0].created_at.tzinfo is UTC
    sql, binds = log[0]
    assert (
        "FROM PLATFORM_CHAT_ATTACHMENTS WHERE PRODUCT = :product "
        "AND CONVERSATION_ID = :conversation_id" in sql
    )
    assert "PARSED_JSON" not in sql and "CONTENT," not in sql
    assert binds["product"] == "agent"


def test_oracle_store_purge_is_bounded() -> None:
    store, log = _oracle()
    store.purge_expired(now=datetime.now(UTC), limit=100)
    assert "WHERE EXPIRES_AT <= :now FETCH FIRST :limit ROWS ONLY" in log[0][0]
    assert log[-1][0] == "COMMIT"


def test_oracle_store_missing_table_is_schema_required() -> None:
    store, _ = _oracle(fail=RuntimeError("ORA-00942: table or view does not exist"))
    with pytest.raises(ChatAttachmentError) as caught:
        store.get("id-1")
    assert (caught.value.status_code, caught.value.code) == (409, "CHAT_ATTACHMENT_SCHEMA_REQUIRED")


def test_ddl_creates_table_outside_auth_tables() -> None:
    ddl = "\n".join(PLATFORM_AUTH_DDL)
    assert f"CREATE TABLE {CHAT_ATTACHMENTS_TABLE}" in ddl
    for column in ("CONTENT BLOB", "PARSED_JSON CLOB", "OUTLINE_JSON CLOB", "EXPIRES_AT TIMESTAMP"):
        assert column in ddl
    assert "IX_PLATFORM_CHAT_ATT_EXPIRES" in ddl
    # 未作成でもログインを止めない（認証の表の判定に入れない）。
    assert CHAT_ATTACHMENTS_TABLE not in PLATFORM_AUTH_TABLES


# ---- 製品ごとの有効・無効（運用設定 › チャットの添付） ----


def test_settings_default_disabled_and_cached() -> None:
    from pr_system_settings.chat_attachments import (
        ChatAttachmentSettingsProvider,
        ChatAttachmentSettingsRecord,
        InMemoryChatAttachmentSettingsStore,
    )

    ticks = iter([0.0, 1.0, 2.0, 20.0])
    store = InMemoryChatAttachmentSettingsStore()
    provider = ChatAttachmentSettingsProvider(store, clock=lambda: next(ticks))
    assert provider.enabled() is False
    store.save(ChatAttachmentSettingsRecord(enabled=True))
    assert provider.enabled() is False  # cache の期限の中
    assert provider.save(ChatAttachmentSettingsRecord(enabled=True)).enabled
    assert provider.enabled() is True


def test_settings_unreadable_store_is_disabled() -> None:
    from pr_system_settings.chat_attachments import ChatAttachmentSettingsProvider

    class _Broken:
        def load(self) -> Any:
            raise RuntimeError("ORA-12541")

        def save(self, record: Any) -> Any:
            raise AssertionError

    assert ChatAttachmentSettingsProvider(_Broken()).enabled() is False


def test_disabled_product_rejects_upload_and_send_and_hides_tools() -> None:
    store = InMemoryChatAttachmentStore()
    enabled = [True]
    service = ChatAttachmentService(
        store, product="agent", settings=lambda: SimpleNamespace(), enabled=lambda: enabled[0]
    )
    item = _upload(
        service, ALICE, "a.txt", b"x", conversation_id="c1", conversation_owner_user_uuid="user-a"
    )
    assert service.toolset("c1")
    enabled[0] = False
    with pytest.raises(ChatAttachmentError) as caught:
        _upload(service, ALICE, "b.txt", b"x")
    assert (caught.value.status_code, caught.value.code) == (403, "CHAT_ATTACHMENT_DISABLED")
    with pytest.raises(ChatAttachmentError):
        service.prepare_attachments(ALICE, [item.attachment_id], conversation_id="c1")
    assert not service.toolset("c1")
    assert service.list_items(ALICE).limits.enabled is False


def test_settings_api_and_oracle_sql() -> None:
    from pr_system_settings.chat_attachments import (
        ChatAttachmentSettingsProvider,
        InMemoryChatAttachmentSettingsStore,
        build_chat_attachment_settings_router,
    )
    from pr_system_settings.chat_attachments.settings import (
        OracleChatAttachmentSettingsStore,
        chat_attachment_setting_key,
    )

    provider = ChatAttachmentSettingsProvider(InMemoryChatAttachmentSettingsStore())
    app = FastAPI()
    app.include_router(
        build_chat_attachment_settings_router(
            load=provider.load,
            save=provider.save,
            get_settings=lambda: SimpleNamespace(chat_attachment_max_files=3),
        ),
        prefix="/api/settings",
    )
    test_client = TestClient(app)
    first = test_client.get("/api/settings/chat-attachments").json()["data"]
    assert (first["enabled"], first["max_files"], first["ttl_hours"]) == (False, 3, 24)
    saved = test_client.put("/api/settings/chat-attachments", json={"enabled": True}).json()["data"]
    assert saved["enabled"] is True and provider.enabled() is True

    log: list[tuple[str, Any]] = []

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor(log, [(1, datetime(2026, 10, 19))], None)

        def commit(self) -> None:
            log.append(("COMMIT", None))

    @contextmanager
    def factory() -> Any:
        yield _Connection()

    oracle = OracleChatAttachmentSettingsStore(factory, chat_attachment_setting_key("rag"))
    loaded = oracle.load()
    assert loaded is not None and loaded.enabled is True
    assert log[0][1] == {"setting_key": "chat_attachments:rag"}
    oracle.save(loaded)
    assert log[1][0].startswith("MERGE INTO PLATFORM_CHAT_ATTACHMENT_SETTINGS")
    assert "CREATE TABLE PLATFORM_CHAT_ATTACHMENT_SETTINGS" in "\n".join(PLATFORM_AUTH_DDL)
