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
from mcp_support import FakeProductMcp
from pytest import MonkeyPatch
from test_answer_validation import AGENT_ID, ANSWER, USER_UUID, _script, mcp  # noqa: F401 - fixture

from app.features.agent import builtin_runtime
from app.features.agent.answer_evidence import ANSWER_EVIDENCE_KIND, answer_evidence
from app.features.agent.answer_passages import is_lookup_note
from app.features.agent.answer_validation import (
    UNVERIFIED_HEADING,
    combine_validations,
    validation_content,
    without_lookup_notes,
)
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


def test_passed_evidence_is_deduplicated_and_cited_by_ids_and_the_validation() -> None:
    steps = [
        _step("rag_retrieve_evidence", {"evidence": [_evidence(CHUNK_A), _evidence(CHUNK_B)]}),
        # 同じ chunk を別の呼び出しでも返す（1 回だけ数える）。RAG の使われ方は
        # 引用したときだけ残す。
        _step(
            "rag_search",
            {
                "evidence": [
                    _evidence(CHUNK_B, used_in_answer=True, answer_use="reference"),
                    _evidence(CHUNK_C, used_in_answer=True, answer_use="confirmed"),
                ]
            },
        ),
        # 失敗したツール・根拠を返さないツールは数えない。
        _step("rag_search", {"evidence": [_evidence("doc-x:cs:1")]}, success=False),
        _step("rag_read_source", {"document_id": "doc-y", "chunk_id": "doc-y:cs:1"}),
    ]
    validation = validation_content(
        "completed",
        result={"claims": [{"status": "supported", "chunk_id": CHUNK_B, "answer_quote": "x"}]},
    )
    # 回答は CHUNK_A を途中を省いた形で書く（最終の検証と同じ照合で 1 つに決まる）。
    content = answer_evidence(steps, "期限は 30 日です【証拠 ID: 0f3a9c2…:7】。", validation)

    items = content["items"]
    assert [item["chunk_id"] for item in items] == [CHUNK_A, CHUNK_B, CHUNK_C]
    assert [item["cited"] for item in items] == [True, True, False]
    assert [item["answer_use"] for item in items] == [None, "reference", None]
    assert (content["passed"], content["cited"]) == (3, 2)
    assert items[0]["section_title"] == "第 1 節"
    assert items[0]["locator"]["page_start"] == 3
    assert "section" not in items[0]
    # 根拠の ID の印は引き当てた根拠と一緒に返す（画面は印を文字で出さず「根拠：…」の行にする）。
    assert content["markers"] == [{"text": "【証拠 ID: 0f3a9c2…:7】", "chunk_ids": [CHUNK_A]}]
    assert content["passages"] == [
        {"text": "期限は 30 日です【証拠 ID: 0f3a9c2…:7】。", "chunk_ids": [CHUNK_A]}
    ]


def test_passages_join_the_validated_chunks_and_skip_unknown_ones() -> None:
    steps = [_step("rag_search", {"evidence": [_evidence(CHUNK_A), _evidence(CHUNK_B)]})]
    answer = (
        "期限は 30 日です。\n- 延長は 1 回だけです。\n- 詳しくは [手順](https://example.com) です。"
    )
    validation = validation_content(
        "completed",
        result={
            "claims": [
                {"status": "supported", "chunk_id": CHUNK_B, "answer_quote": "期限は 30 日です。"},
                {"status": "supported", "chunk_id": CHUNK_A, "answer_quote": "期限は 30 日です。"},
                # 前のターンの根拠（この Run で渡していない chunk）は段落の根拠にしない。
                {
                    "status": "supported",
                    "chunk_id": "doc-z:cs:1",
                    "answer_quote": "- 延長は 1 回だけです。",
                },
                {"status": "unsupported", "chunk_id": CHUNK_A, "answer_quote": "x"},
            ]
        },
    )
    content = answer_evidence(steps, answer, validation)
    assert content["markers"] == []
    assert content["passages"] == [{"text": "期限は 30 日です。", "chunk_ids": [CHUNK_A, CHUNK_B]}]


def test_nothing_is_cited_without_written_ids_or_a_completed_validation() -> None:
    steps = [_step("rag_search", {"evidence": [_evidence(CHUNK_A, used_in_answer=True)]})]
    unvalidated = validation_content("unvalidated", reason="validation_error")
    content = answer_evidence(steps, "期限は 30 日です。", unvalidated)
    assert (content["passed"], content["cited"]) == (1, 0)


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


def test_the_notice_listing_counts_what_the_answer_section_shows() -> None:
    quotes = [f"段落 {index} は 30 日です。" for index in range(7)]
    answer = "\n".join(quotes)
    result = {
        "valid": False,
        "status": "completed",
        "claims": [
            {"answer_quote": quote, "status": "unsupported", "reason": "x"} for quote in quotes
        ],
        "findings": [],
        "missing_evidence": [],
        "stale_evidence": [],
    }
    content, published = combine_validations(
        answer, [validation_content("completed", connection="rag", result=result)]
    )
    assert f"**{UNVERIFIED_HEADING}**" in published
    assert "- ほか 2 件の主張" in published
    # 節に書いたのは先頭の 5 件。画面の「回答の確かめ」は残りだけを出す。
    assert content["notice"] == {
        "claims": 5,
        "findings": 0,
        "evidence": True,
        "heading": UNVERIFIED_HEADING,
    }
    assert content["withheld"] == {"claims": 7, "findings": 0, "all": True}


def test_the_run_saves_the_answer_evidence_and_drops_lookup_notes_before_validation(
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

    # 調べ方の行は検証に渡さず、回答にも「資料で確認できず除いた内容」にも出さない。
    [call] = mcp.calls_of("rag_validate_answer")
    assert call["arguments"]["answer"] == ANSWER
    published = run_answer_text(run)
    assert published == ANSWER
    assert "0f3a9c2e7b1d4e5f" not in (published or "")
    # 回答の根拠: AI に渡した 1 件、最終の検証が裏付けた 1 件を回答で引用。
    [artifact] = [item for item in run.artifacts if item.kind == ANSWER_EVIDENCE_KIND]
    assert artifact.name == "回答の根拠"
    assert (artifact.content["passed"], artifact.content["cited"]) == (1, 1)
    assert artifact.content["items"][0]["chunk_id"] == "chunk-1"
