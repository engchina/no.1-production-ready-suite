"""回答の根拠（成果物 `answer_evidence`）と、調べ方の行の扱い（#2328）の決定論テスト。

- AI に渡した根拠 = その Run の `rag_search` / `rag_retrieve_evidence` の `evidence`
  （chunk で重複なし）。
- 回答で引用 = 回答が書いた根拠の ID（省いた形を含む）と、最終の検証が段落を裏付けた根拠。
- 調べるのに使ったプロファイル・ツール・内部の ID だけを述べる行は、検証の前に回答から外し、
  「資料で確認できず除いた内容」に書かない。
内容はすべて合成のデータ。
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from agents.testing import assistant_message, function_call
from answer_run_fixtures import AGENT_ID, ANSWER, USER_UUID, _script, mcp  # noqa: F401 - fixture
from mcp_support import FakeProductMcp
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.answer_evidence import (
    ANSWER_EVIDENCE_KIND,
    answer_evidence,
    passed_evidence,
)
from app.features.agent.answer_passages import is_lookup_note
from app.features.agent.answer_references import without_lookup_notes
from app.features.agent.runtime import (
    RunCreateRequest,
    RunStep,
    StepStatus,
    run_answer_text,
    runtime_repository,
)
from app.features.agent.tools import ToolCall, ToolResult

DOC = "0f3a9c2e7b1d4e5f"
CHUNK_A = f"{DOC}:cs_a:7"
CHUNK_B = f"{DOC}:cs_a:12"
CHUNK_C = "doc-c:cs_c:1"


def _evidence(chunk_id: str, **extra: Any) -> dict[str, Any]:
    document_id = chunk_id.split(":", 1)[0]
    return {
        "evidence_id": chunk_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "file_name": f"文書 {document_id[:4]}.pdf",
        "excerpt": "合成の本文。",
        "truncated": False,
        "locator": {"page_start": 3, "page_end": 3, "section_path": ["第 1 章"]},
        "role": "retrieved_anchor",
        "score": 0.8,
        "rerank_score": 0.6,
        "section": {"section_id": "s1", "title": "第 1 節", "level": 2, "chars": 10},
        **extra,
    }


def _step(tool: str, output: dict[str, Any], *, success: bool = True) -> RunStep:
    return RunStep(
        run_id="run-evidence",
        status=StepStatus.COMPLETED if success else StepStatus.FAILED,
        tool_call=ToolCall(name=f"rag__{tool}", arguments={}),
        tool_result=ToolResult(name=f"rag__{tool}", success=success, output=output),
    )


ATTACHMENT = "5d1c7a2e-0000-4000-8000-00000000000a"
SHEET_ATTACHMENT = "5d1c7a2e-0000-4000-8000-00000000000b"


def _attachment_step(tool: str, output: dict[str, Any], *, success: bool = True) -> RunStep:
    return RunStep(
        run_id="run-evidence",
        status=StepStatus.COMPLETED if success else StepStatus.FAILED,
        tool_call=ToolCall(name=tool, arguments={}),
        tool_result=ToolResult(name=tool, success=success, output=output),
    )


def test_attachment_reads_become_evidence_that_is_not_opened_as_a_rag_source() -> None:
    """添付を読むツールの結果は根拠の項目になる（#2554）。RAG の根拠の扱いは変えない。"""
    listing = {
        "attachments": [
            {
                "attachment_id": ATTACHMENT,
                "file_name": "manual-a.pdf",
                "sections": [{"section_id": "s3", "title": "第 3 章", "page": 3}],
                "sheets": [],
            }
        ]
    }
    steps = [
        _step("rag_search", {"evidence": [_evidence(CHUNK_A)]}),
        _attachment_step("chat_attachment_list", listing),
        _attachment_step(
            "chat_attachment_read_section",
            {
                "attachment_id": ATTACHMENT,
                "file_name": "manual-a.pdf",
                "section_id": "s1",
                "title": "p.1",
                "page": 1,
                "content": "合成の本文。" * 500,
                "next_offset": None,
            },
        ),
        _attachment_step(
            "chat_attachment_read_rows",
            {
                "attachment_id": SHEET_ATTACHMENT,
                "file_name": "table-b.csv",
                "sheet": "table-b",
                "columns": ["code", "amount"],
                "rows": [
                    {"row_number": 1, "values": {"code": "A", "amount": 100}},
                    {"row_number": 2, "values": {"code": "B", "amount": None}},
                ],
                "row_count": 2,
                "next_start_row": None,
            },
        ),
        # 一致した節の頁・見出しは、同じ Run の一覧から引く。同じ節は 1 回だけ。
        _attachment_step(
            "chat_attachment_find",
            {
                "matches": [
                    {
                        "attachment_id": ATTACHMENT,
                        "file_name": "manual-a.pdf",
                        "section_id": "s3",
                        "sheet": None,
                        "row_number": None,
                        "snippet": "…費用は 1 回あたり 100 です…",
                    },
                    {
                        "attachment_id": ATTACHMENT,
                        "file_name": "manual-a.pdf",
                        "section_id": "s1",
                        "sheet": None,
                        "row_number": None,
                        "snippet": "…合成の本文…",
                    },
                ],
                "truncated": False,
            },
        ),
        # 失敗した呼び出しは数えない。
        _attachment_step(
            "chat_attachment_read_section",
            {"attachment_id": ATTACHMENT, "section_id": "s9", "content": "x"},
            success=False,
        ),
    ]
    # モデルはファイル名の「-」を別のダッシュに書き換えることがある。
    content = answer_evidence(steps, "費用は 100 です（出典：manual‑a.pdf p.3）。")

    items = content["items"]
    assert [item["chunk_id"] for item in items] == [
        CHUNK_A,
        f"attachment:{ATTACHMENT}:s1",
        f"attachment:{SHEET_ATTACHMENT}:table-b:1-2",
        f"attachment:{ATTACHMENT}:s3",
    ]
    attachment_items = items[1:]
    assert {item["document_id"] for item in attachment_items} == {
        f"attachment:{ATTACHMENT}",
        f"attachment:{SHEET_ATTACHMENT}",
    }
    assert [item["chat_attachment_id"] for item in attachment_items] == [
        ATTACHMENT,
        SHEET_ATTACHMENT,
        ATTACHMENT,
    ]
    # 回答がファイル名を書いた添付だけを「回答で引用」にする。
    assert [item["cited"] for item in items] == [False, True, False, True]
    assert (content["passed"], content["cited"]) == (4, 2)
    section, rows, found = attachment_items
    assert section["locator"] == {"page_start": 1, "page_end": 1}
    # PDF の節の見出し「p.N」は頁と重ねない。
    assert "section_title" not in section
    assert section["truncated"] is True
    assert len(section["excerpt"]) == 2_000
    assert rows["locator"] == {"sheet_name": "table-b", "row_start": 1, "row_end": 2}
    assert rows["excerpt"] == "code: A / amount: 100\ncode: B"
    assert rows["content_kind"] == "sheet"
    assert found["locator"] == {"page_start": 3, "page_end": 3}
    assert found["section_title"] == "第 3 章"
    # 根拠の原文・図を開く確かめ（RAG の文書）には添付を入れない。
    assert [item["chunk_id"] for item in passed_evidence(steps)] == [CHUNK_A]


def test_attachment_evidence_alone_is_listed_without_a_validation() -> None:
    steps = [
        _attachment_step(
            "chat_attachment_find",
            {
                "matches": [
                    {
                        "attachment_id": SHEET_ATTACHMENT,
                        "file_name": "table-b.csv",
                        "section_id": None,
                        "sheet": "table-b",
                        "row_number": 4,
                        "snippet": "code: D / amount: 400",
                    }
                ],
                "truncated": False,
            },
        )
    ]
    content = answer_evidence(steps, "金額は 400 です。")
    assert [item["chunk_id"] for item in content["items"]] == [
        f"attachment:{SHEET_ATTACHMENT}:table-b:4-4"
    ]
    assert (content["passed"], content["cited"]) == (1, 0)


# ---- 照合が成功 / 失敗 / 時間切れの結び付け（#2707） -----------------------------------

# 合成の根拠: A は申請の期限、B は承認、C は別の文書の手当て。A と B は同じ文書の別の頁。
EXCERPT_A = "合成の規程では、申請書は利用開始日の 30 日前までに提出する。提出先は窓口 X とする。"
EXCERPT_B = "承認は担当部署の課長が行う。課長が不在のときは代理者が承認する。"
EXCERPT_C = "<table><tr><td>区分 Q-77</td><td>手当は月額 5000 円</td></tr></table>"
# モデルは根拠の ID の印を書かず、文書名と頁を「根拠」の節に平文で並べる
# （照合が時間切れの Run の形）。
FREE_ANSWER = "\n".join(
    [
        "合成の質問への回答です。",
        "",
        "- 申請書は利用開始日の 30 日前までに提出してください。",
        "- 承認は担当部署の課長が行います。",
        "- 区分 Q-77 の手当があります。",
        "- 不明な点は窓口へ確認してください。",
        "",
        "**根拠**",
        "",
        "- 文書 0f3a.pdf p.3",
        "- 文書 0f3a.pdf p.5",
    ]
)


def _free_steps() -> list[RunStep]:
    return [
        _step(
            "rag_search",
            {
                "evidence": [
                    _evidence(CHUNK_A, excerpt=EXCERPT_A, answer_use="confirmed"),
                    _evidence(
                        CHUNK_B,
                        excerpt=EXCERPT_B,
                        locator={"page_start": 5, "page_end": 5, "section_path": ["第 2 章"]},
                        section={"section_id": "s2", "title": "第 2 節", "level": 2, "chars": 10},
                    ),
                    _evidence(CHUNK_C, excerpt=EXCERPT_C, file_name="文書 C.pdf"),
                ]
            },
        )
    ]


def test_a_citation_line_without_a_page_points_at_the_first_evidence_of_the_document() -> None:
    content = answer_evidence(_free_steps(), "期限があります。\n\n出典：文書 C.pdf")
    assert content["passages"] == [{"text": "出典：文書 C.pdf", "chunk_ids": [CHUNK_C]}]
    # 主張の段落の頁の無い文書名は、文書名だけでは結び付けない。
    claim = answer_evidence(_free_steps(), "文書 C.pdf に記載があります。")
    assert claim["passages"] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- プロファイル A（ID: 0f3a9c2e7b1d4e5f）を用いて上記根拠を取得しました。", True),
        ("検索の設定 B（ID:3fa85f64-5717-4562-b3fc-2c963f66afa6）で検索しました。", True),
        ("rag_retrieve_evidence で根拠を取得しました。", True),
        # 内部の ID の無い調べ方・主張・数字だけの番号・操作の指示は外さない。
        ("第 3 条を確認しました。", False),
        ("承認者は部長です（ID: 1a2b3c4d）。", False),
        ("申請番号 20240101 を照会しました。", False),
        ("設定画面で ID: 12 を入力して保存してください。", False),
    ],
    ids=["profile-id", "uuid", "tool-name", "no-id", "claim", "digits", "operation"],
)
def test_lookup_notes_are_lines_that_only_name_the_tool_profile_or_internal_id(
    text: str, expected: bool
) -> None:
    assert is_lookup_note(text) is expected


def test_lookup_notes_are_removed_without_emptying_the_answer() -> None:
    note = "- プロファイル A（ID: 0f3a9c2e7b1d4e5f）を用いて上記根拠を取得しました。"
    answer = f"期限は 30 日です。\n\n**根拠**\n\n{note}"
    # 中身が無くなった節の見出しも消す（`withhold_paragraphs` と同じ）。
    assert without_lookup_notes(answer) == "期限は 30 日です。"
    # 外すと何も残らない回答はそのまま。
    assert without_lookup_notes(note) == note
    assert without_lookup_notes("期限は 30 日です。") == "期限は 30 日です。"


def test_the_run_saves_the_answer_evidence_and_drops_lookup_notes(
    monkeypatch: MonkeyPatch,
    mcp: FakeProductMcp,  # noqa: F811 - fixture
) -> None:
    note = "- プロファイル A（ID: 0f3a9c2e7b1d4e5f）を用いて上記根拠を取得しました。"
    answer = f"{ANSWER}\n\n{note}"
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "合成の質問"}, call_id="call-1")],
        [assistant_message(answer)],
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    run = runtime_repository.get_run(run.id)

    # 調べ方の行は回答に残さない。
    published = run_answer_text(run)
    assert published == ANSWER
    assert "0f3a9c2e7b1d4e5f" not in (published or "")
    # 回答の根拠: AI に渡した 1 件（ID も原文の写しも書いていない回答なので引用は 0 件）。
    [artifact] = [item for item in run.artifacts if item.kind == ANSWER_EVIDENCE_KIND]
    assert artifact.name == "回答の根拠"
    assert (artifact.content["passed"], artifact.content["cited"]) == (1, 0)
    assert artifact.content["items"][0]["chunk_id"] == "chunk-1"
