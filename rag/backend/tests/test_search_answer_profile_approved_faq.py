"""検索・回答プロファイル単位の承認済み FAQ（質問と回答だけ。#5107）の API。"""

import io
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]

from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.config import get_settings
from app.main import app
from app.rag import approved_faq_answer
from tests.support import AsgiTestClient
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/search-answer-profiles/bv-1/approved-faq"


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(approved_faq_answer, "OciGenAiClient", StubGenAi)
    return fake


class StubGenAi:
    """意味照合の embedding を決定論化する(外部 OCI を呼ばない)。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def embed(self, texts: list[str], *, input_type: str = "") -> list[list[float]]:
        return [[1.0 if "取り消" in text else 0.0, 1.0] + [0.0] * 1534 for text in texts]


def _excel(header: list[str], rows: list[tuple[str, ...]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(header)
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
    assert records == [
        {"id": records[0]["id"], "question": "受注を取り消すには？", "answer": records[0]["answer"]}
    ]
    faq_id = records[0]["id"]

    listed = client.get(BASE).json()["data"]
    assert listed["records"][0]["answer"] == "受注一覧で取消を押します。"
    # 候補の下限と件数は全体設定の値（画面の説明に出す。設定欄は無い）。
    assert (listed["min_score"], listed["max_suggestions"]) == (0.92, 3)

    suggested = client.post(f"{BASE}/suggest", json={"query": "受注を取り消すには？"})
    assert suggested.json()["data"] == {
        "suggestions": [
            {
                "id": faq_id,
                "question": "受注を取り消すには？",
                "answer": "受注一覧で取消を押します。",
                "score": 1.0,
            }
        ],
        "question_context_token": None,
    }
    # 一致度が下限未満の FAQ は出さない。
    unrelated = client.post(f"{BASE}/suggest", json={"query": "在庫の棚卸し手順"})
    assert unrelated.json()["data"]["suggestions"] == []

    deleted = client.post(f"{BASE}/delete", json={"ids": [faq_id]})
    assert deleted.json()["data"]["deleted_count"] == 1
    assert client.get(BASE).json()["data"]["records"] == []


def test_removed_fields_and_endpoints_are_gone(fake_oracle: FakeKnowledgeOracle) -> None:
    """当てはめない質問・対象のナレッジベース・確かめ直しは無い（#5107）。"""
    added = client.post(
        BASE,
        json={
            "question": "合成の質問",
            "answer": "合成の回答",
            "negative_questions": ["別の質問"],
            "target_knowledge_base_ids": ["kb-1"],
        },
    )
    assert added.status_code == 200
    records = fake_oracle.payloads[("bv-1", "approved_faq")]["records"]
    assert isinstance(records, list) and len(records) == 1
    record = records[0]
    assert set(record) == {"id", "question", "approved_answer", "status", "source", "timestamps"}
    faq_id = record["id"]
    assert client.post(f"{BASE}/{faq_id}/confirm", json={}).status_code in {404, 405}
    negative = client.put(f"{BASE}/{faq_id}/negative-questions", json={"negative_questions": []})
    assert negative.status_code in {404, 405}


def test_excel_reads_only_question_and_answer_columns(fake_oracle: FakeKnowledgeOracle) -> None:
    content = _excel(
        ["QUESTION", "ANSWER", "NEGATIVE_QUESTIONS", "TARGET_KNOWLEDGE_BASES"],
        [
            ("受注を登録するには？", "受注入力画面で登録します。", "別の質問", "kb-9"),
            ("", "", "", ""),
        ],
    )
    files = {"file": ("faq.xlsx", content, "application/octet-stream")}

    preview = client.post(f"{BASE}/import/preview", files=files)
    assert preview.status_code == 200
    assert preview.json()["data"] == {
        "total": 1,
        "rows": [
            {"question": "受注を登録するには？", "answer": "受注入力画面で登録します。", "row": 2}
        ],
    }

    imported = client.post(f"{BASE}/import", files=files, data={"mode": "INSERT"})
    assert imported.json()["data"]["inserted_count"] == 1
    again = client.post(f"{BASE}/import", files=files, data={"mode": "INSERT"})
    assert again.json()["data"]["inserted_count"] == 0
    assert len(again.json()["data"]["records"]) == 1

    bad = client.post(f"{BASE}/import/preview", files={"file": ("faq.csv", b"a,b", "text/csv")})
    assert bad.status_code == 422


def test_template_has_only_the_question_and_answer_columns(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    response = client.get(f"{BASE}/import/template")

    assert response.status_code == 200
    assert 'filename="approved_faq_template.xlsx"' in response.headers["content-disposition"]
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert [cell.value for cell in sheet[1]] == ["QUESTION", "ANSWER"]
    assert sheet.max_row == 1
    # テンプレートをそのまま取り込める形（行が無いので取込行は 0 件の扱い）。
    assert (
        client.get("/api/search-answer-profiles/bv-9/approved-faq/import/template").status_code
        == 404
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"question": "  ", "answer": "a"}, "質問を入力してください。"),
        ({"question": "q", "answer": ""}, "回答を入力してください。"),
    ],
    ids=["question", "answer"],
)
def test_add_rejects_blank_fields_like_the_screen(
    fake_oracle: FakeKnowledgeOracle, payload: dict[str, str], message: str
) -> None:
    """FAQ の追加の未入力は、画面の欄の下と同じ文言で欄を指す 422（#541）。"""
    response = client.post(BASE, json=payload)
    assert response.status_code == 422
    assert response.json()["error_messages"] == [message]


def test_suggestion_toggle_is_kept_across_faq_edits_and_stops_suggestions(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    """候補の提示は検索・回答プロファイルごとにオン / オフでき、未設定はオン(#684)。"""
    assert client.get(BASE).json()["data"]["enabled"] is True
    client.post(BASE, json={"question": "受注を取り消すには？", "answer": "取消を押します。"})

    off = client.put(f"{BASE}/settings", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["data"]["enabled"] is False
    # FAQ を追加・削除しても設定は消えない。
    client.post(BASE, json={"question": "受注を登録するには？", "answer": "登録を押します。"})
    assert client.get(BASE).json()["data"]["enabled"] is False
    suggested = client.post(f"{BASE}/suggest", json={"query": "受注を取り消すには？"})
    assert suggested.json()["data"]["suggestions"] == []

    client.put(f"{BASE}/settings", json={"enabled": True})
    suggested = client.post(f"{BASE}/suggest", json={"query": "受注を取り消すには？"})
    assert suggested.json()["data"]["suggestions"]
    # 「そのまま返す」の設定は無い。
    assert client.put(f"{BASE}/settings", json={"direct_answer_enabled": True}).status_code == 422


def test_suggestions_use_the_min_score_and_the_max_count_settings(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補は RAG_APPROVED_FAQ_MIN_SCORE 以上を最大 RAG_APPROVED_FAQ_MAX_SUGGESTIONS 件。"""
    monkeypatch.setattr(get_settings(), "rag_approved_faq_semantic_enabled", False)
    for index in range(5):
        client.post(BASE, json={"question": f"合成の質問 {index}", "answer": f"回答 {index}"})
    client.post(BASE, json={"question": "合成の質問", "answer": "完全一致の回答"})

    monkeypatch.setattr(get_settings(), "rag_approved_faq_min_score", 0.9)
    loose = client.post(f"{BASE}/suggest", json={"query": "合成の質問"}).json()["data"]
    assert len(loose["suggestions"]) == 3
    assert loose["suggestions"][0]["answer"] == "完全一致の回答"

    monkeypatch.setattr(get_settings(), "rag_approved_faq_max_suggestions", 1)
    one = client.post(f"{BASE}/suggest", json={"query": "合成の質問"}).json()["data"]
    assert [item["answer"] for item in one["suggestions"]] == ["完全一致の回答"]

    monkeypatch.setattr(get_settings(), "rag_approved_faq_max_suggestions", 3)
    monkeypatch.setattr(get_settings(), "rag_approved_faq_min_score", 0.99)
    strict = client.post(f"{BASE}/suggest", json={"query": "合成の質問 9"}).json()["data"]
    assert strict["suggestions"] == []


def test_suggest_is_judged_by_the_profile_permission_not_by_knowledge_bases(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補はプロファイルの権限だけで判定し、参照 KB を使えなくても 403 にしない（#5107）。"""
    from tests.security_support import enable_production_auth, login

    client.post(BASE, json={"question": "合成の設定 A はどこですか？", "answer": "画面 A です。"})
    auth = enable_production_auth(monkeypatch)
    # プロファイル bv-1 は許可、参照 KB の kb-1 は不許可。
    auth.user_with_permissions(
        "no-kb", ["menu.search"], search_answer_profile_ids=["bv-1"], knowledge_base_ids=["kb-9"]
    )
    query = {"query": "合成の設定 A はどこですか？"}

    allowed = client.post(f"{BASE}/suggest", json=query, headers=login(client, "no-kb"))

    assert allowed.status_code == 200
    assert [item["answer"] for item in allowed.json()["data"]["suggestions"]] == ["画面 A です。"]


def test_chat_suggest_completes_the_question_first_and_returns_its_token(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """チャットは安全チェック → 補完 → 照合の順で、補完の token を返す（#5107）。"""
    from app.api.routes import chat as chat_route

    calls: list[tuple[str, str | None]] = []

    async def prepare(
        _oracle: object,
        conversation_id: str | None,
        _profile: str,
        query: str,
        *_a: object,
        **kwargs: Any,
    ) -> tuple[str, str]:
        calls.append((query, conversation_id))
        assert kwargs["prechecked"].allowed is True
        return "受注を取り消すには？", "token-1"

    monkeypatch.setattr(chat_route, "prepare_chat_question", prepare)
    body = {"query": "それを取り消すには？", "conversation_id": "conv-1"}

    # FAQ が無いときは補完も照合もしない（確認の照会が補完する）。
    empty = client.post(f"{BASE}/suggest", json=body).json()["data"]
    assert empty == {"suggestions": [], "question_context_token": None}
    assert calls == []

    client.post(BASE, json={"question": "受注を取り消すには？", "answer": "取消を押します。"})
    data = client.post(f"{BASE}/suggest", json=body).json()["data"]

    assert calls == [("それを取り消すには？", "conv-1")]
    assert data["question_context_token"] == "token-1"
    assert [item["answer"] for item in data["suggestions"]] == ["取消を押します。"]
