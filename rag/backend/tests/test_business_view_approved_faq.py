"""業務ビュー単位の Approved FAQ(類似問)API。"""

import io

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.api.routes import business_view_knowledge as knowledge_route
from app.main import app
from tests.support import AsgiTestClient
from tests.test_business_view_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/business-views/bv-1/approved-faq"


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    return fake


def _excel(rows: list[tuple[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["QUESTION", "ANSWER"])
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_add_list_suggest_and_delete_approved_faq(fake_oracle: FakeKnowledgeOracle) -> None:
    added = client.post(
        BASE, json={"question": "受注を取り消すには？", "answer": "受注一覧で取消を押します。"}
    )
    assert added.status_code == 200
    records = added.json()["data"]["records"]
    assert [record["question"] for record in records] == ["受注を取り消すには？"]
    faq_id = records[0]["id"]

    listed = client.get(BASE).json()["data"]["records"]
    assert listed[0]["answer"] == "受注一覧で取消を押します。"

    suggested = client.post(f"{BASE}/suggest", json={"query": "受注を取り消すには？"})
    suggestion = suggested.json()["data"]["suggestions"][0]
    assert suggestion["id"] == faq_id
    assert suggestion["direct"] is True
    unrelated = client.post(f"{BASE}/suggest", json={"query": "在庫の棚卸し手順"})
    assert all(not item["direct"] for item in unrelated.json()["data"]["suggestions"])

    deleted = client.post(f"{BASE}/delete", json={"ids": [faq_id]})
    assert deleted.json()["data"]["deleted_count"] == 1
    assert client.get(BASE).json()["data"]["records"] == []


def test_excel_preview_and_import_modes(fake_oracle: FakeKnowledgeOracle) -> None:
    content = _excel([("受注を登録するには？", "受注入力画面で登録します。"), ("", "")])
    files = {"file": ("faq.xlsx", content, "application/octet-stream")}

    preview = client.post(f"{BASE}/import/preview", files=files)
    assert preview.status_code == 200
    assert preview.json()["data"]["total"] == 1
    assert preview.json()["data"]["rows"][0]["question"] == "受注を登録するには？"

    imported = client.post(f"{BASE}/import", files=files, data={"mode": "INSERT"})
    assert imported.json()["data"]["inserted_count"] == 1
    again = client.post(f"{BASE}/import", files=files, data={"mode": "INSERT"})
    assert again.json()["data"]["inserted_count"] == 0
    assert len(again.json()["data"]["records"]) == 1

    bad = client.post(f"{BASE}/import/preview", files={"file": ("faq.csv", b"a,b", "text/csv")})
    assert bad.status_code == 422
