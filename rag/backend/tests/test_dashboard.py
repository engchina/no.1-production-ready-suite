"""ダッシュボード API のテスト。"""

from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_dashboard_summary_returns_zero_state() -> None:
    """データがない状態でもダッシュボード契約を返す。"""
    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stats"] == {
        "total_uploads": 0,
        "uploads_this_month": 0,
        "total_registrations": 0,
        "registrations_this_month": 0,
        "total_categories": 5,
        "active_categories": 5,
        "searchable_rows": 0,
    }
    assert data["recent_activities"] == []
    assert data["system"]["status"] == "online"
    assert data["system"]["adapter"] == "local"
    assert data["system"]["checks"] == {"local_storage": "ok"}


def test_dashboard_summary_reflects_documents_and_indexed_chunks() -> None:
    """アップロード・分析・登録済み状態を集計と最近の活動へ反映する。"""
    registered_id = _upload("invoice-a.txt", "請求書番号: INV-001\nクラウド利用料".encode())
    analyze_resp = client.post(f"/api/documents/{registered_id}/analyze")
    assert analyze_resp.status_code == 200
    register_resp = client.post(f"/api/documents/{registered_id}/register")
    assert register_resp.status_code == 200

    uploaded_id = _upload("invoice-b.txt", "未分析の請求書".encode())

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    stats = data["stats"]
    assert stats["total_uploads"] == 2
    assert stats["uploads_this_month"] == 2
    assert stats["total_registrations"] == 1
    assert stats["registrations_this_month"] == 1
    assert stats["searchable_rows"] >= 1

    activities = data["recent_activities"]
    assert {activity["id"] for activity in activities} == {registered_id, uploaded_id}
    registered_activity = next(
        activity for activity in activities if activity["id"] == registered_id
    )
    assert registered_activity["type"] == "REGISTRATION"
    assert registered_activity["status"] == "REGISTERED"
    uploaded_activity = next(activity for activity in activities if activity["id"] == uploaded_id)
    assert uploaded_activity["type"] == "UPLOAD"
    assert uploaded_activity["status"] == "UPLOADED"
    assert data["system"]["searchable_rows"] == stats["searchable_rows"]


def _upload(file_name: str, content: bytes) -> str:
    response = client.post(
        "/api/documents/upload",
        files={"file": (file_name, content, "text/plain")},
    )
    assert response.status_code == 200
    return str(response.json()["data"]["id"])
