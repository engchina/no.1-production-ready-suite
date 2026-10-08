"""回答の最終の検証（#1246）。根拠は今の権限と版で読み直し、主張の監査（モデル）はスタブ。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pytest import MonkeyPatch

from app.clients import support_guide_store
from app.config import get_settings
from app.mcp import tools as mcp_tools
from app.rag.answer_checks import (
    AnswerFinding,
    check_guide_steps,
    check_impact,
    check_requests,
    excluded_steps,
    impact_applies,
)
from app.rag.answer_validation import (
    AnswerValidation,
    EvidenceRef,
    GuideCheckRef,
    GuideProfileNotFoundError,
    is_valid,
    validate_answer,
)
from app.schemas.search import RetrievedChunk
from app.schemas.support_guide import SupportGuideContent
from tests.test_mcp_api import _call, _token, auth  # noqa: F401 - fixture を使う

SUPPORT_GUIDES = (
    Path(__file__).resolve().parents[2] / "evaluation/business-support/support-guides.json"
)


def test_validity_rules() -> None:
    assert is_valid("completed", {"supported": 2, "data_confirmation": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 2, "unsupported": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1, "contradicted": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1, "unassessed": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1}, unreadable=1)
    assert not is_valid("completed", {"data_confirmation": 1}, unreadable=0)
    assert not is_valid("input_too_large", {}, unreadable=0)


class _Oracle:
    def __init__(self) -> None:
        self.chunks = {
            ("d1", "c1"): RetrievedChunk(
                document_id="d1",
                chunk_id="c1",
                text="有効期限は最長 30 日です。",
                score=0.5,
                file_name="manual.pdf",
                metadata={"page_start": 1, "page_end": 1},
            )
        }
        self.stale = {("d1", "c-old")}

    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> Any:
        return self.chunks.get((document_id, chunk_id))

    async def accessible_chunk_exists(self, document_id: str, chunk_id: str) -> bool:
        return (document_id, chunk_id) in self.stale


async def test_validate_answer_rereads_evidence_and_reports_unreadable(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_claims(
        question: str, answer: str, items: list[dict[str, Any]], settings: Any
    ) -> dict[str, Any]:
        captured["items"] = items
        return {
            "status": "completed",
            "counts": {"supported": 1},
            "claim_checks": [
                {
                    "answer_quote": answer,
                    "status": "supported",
                    "source_id": "c1",
                    "reason": "根拠に記載",
                }
            ],
            "evidence_truncated": False,
        }

    import rag_engine.evaluation.answer_validation as engine_validation

    monkeypatch.setattr(engine_validation, "validate_answer_claims", fake_claims)
    refs = [
        EvidenceRef("d1", "c1"),
        EvidenceRef("d1", "c-old"),
        EvidenceRef("d9", "x"),
        EvidenceRef("d1", "c1"),
    ]
    result = await validate_answer(
        "q", "有効期限は 30 日です。", refs, get_settings(), oracle=cast(Any, _Oracle())
    )
    # サーバーが読み直した本文だけを監査に渡す（重複は 1 回）。
    assert [item["text"] for item in captured["items"]] == ["有効期限は最長 30 日です。"]
    assert result.stale_evidence == [EvidenceRef("d1", "c-old")]
    assert result.missing_evidence == [EvidenceRef("d9", "x")]
    assert result.valid is False  # 読めない根拠がある

    none = await validate_answer(
        "q", "a", [EvidenceRef("d9", "x")], get_settings(), oracle=cast(Any, _Oracle())
    )
    assert (none.status, none.valid) == ("no_evidence", False)


@pytest.mark.usefixtures("auth")
def test_mcp_validate_answer(auth: Any, monkeypatch: MonkeyPatch) -> None:  # noqa: F811
    async def fake_validate(
        question: str, answer: str, refs: list[EvidenceRef], settings: Any, **kwargs: Any
    ) -> AnswerValidation:
        assert [ref.chunk_id for ref in refs] == ["c1"]
        # 任意の入力が無ければ、決定的な検査はしない（#1276）。
        assert (kwargs["requests"], kwargs["gaps"], kwargs["guide"]) == (None, [], None)
        return AnswerValidation(
            status="completed",
            valid=False,
            counts={"supported": 1, "unsupported": 1},
            claim_checks=[
                {
                    "answer_quote": "30 日です。",
                    "status": "supported",
                    "source_id": "c1",
                    "reason": "記載",
                },
                {
                    "answer_quote": "延長は 90 日。",
                    "status": "unsupported",
                    "source_id": "",
                    "reason": "記載なし",
                },
            ],
        )

    monkeypatch.setattr(mcp_tools, "validate_answer", fake_validate)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_validate_answer",
        {
            "query": "q",
            "answer": "30 日です。延長は 90 日。",
            "evidence": [{"document_id": "d1", "chunk_id": "c1"}],
        },
        _token(user.user_uuid),
    )["structuredContent"]
    assert body["valid"] is False
    assert body["schema_version"] == mcp_tools.MCP_OUTPUT_SCHEMA_VERSION
    assert (body["checks"], body["findings"], body["guide_revision"]) == ([], [], None)
    assert [claim["status"] for claim in body["claims"]] == ["supported", "unsupported"]
    assert body["claims"][0]["chunk_id"] == "c1" and body["claims"][1]["chunk_id"] is None
    chatter = auth.user_with_permissions("chatter", ["menu.chat"])
    denied = _call(
        "rag_validate_answer",
        {"query": "q", "answer": "a", "evidence": [{"document_id": "d1", "chunk_id": "c1"}]},
        _token(chatter.user_uuid),
    )
    assert denied["isError"] is True


# ---- 決定的な検査（#1276）。業務ガイドは架空の「サンプル業務ポータル」の例だけを使う。----


def _guide(**overrides: Any) -> SupportGuideContent:
    payload: dict[str, Any] = {
        "title": "アクセス権限を付与する",
        "goal": {"expected_result": "対象の利用者が機能を使える", "match_terms": ["権限を付与"]},
        "conditions": [
            {
                "id": "target",
                "label": "付与先",
                "allowed_values": ["個別", "グループ"],
                "question": "権限は個別の利用者とグループのどちらに付けますか？",
            }
        ],
        "steps": [
            {"id": "open", "title": "権限タブを開く"},
            {"id": "user", "title": "利用者を選ぶ", "depends_on": ["open"]},
            {"id": "group", "title": "グループを選ぶ", "depends_on": ["open"]},
            {"id": "group_save", "title": "グループの設定を保存する", "depends_on": ["group"]},
            {"id": "verify", "title": "付与結果を確認する", "depends_on": ["user", "group_save"]},
        ],
        "branches": [
            {
                "id": "b1",
                "when": {"condition_id": "target", "values": ["個別"]},
                "goto_step": "user",
            },
            {
                "id": "b2",
                "when": {"condition_id": "target", "values": ["グループ"]},
                "goto_step": "group",
            },
        ],
        "impact": {"scope": "individual"},
    }
    payload.update(overrides)
    return SupportGuideContent.model_validate(payload)


def _codes(findings: list[AnswerFinding]) -> list[tuple[str, str]]:
    return [(item.code, item.severity) for item in findings]


def test_check_requests_reports_silent_and_disclosed_gaps() -> None:
    requests = [
        {"id": "Q1", "text": "期限", "status": "addressed"},
        {"id": "Q2", "text": "例外の扱い", "status": "missing"},
        {"id": "Q3", "text": "申請先", "status": "partial"},
        {"id": "Q4", "text": "様式", "status": "unknown"},
    ]
    silent = check_requests("期限は 30 日です。", requests)
    assert _codes(silent) == [
        ("request_missing", "error"),
        ("request_partial", "error"),
        ("request_unverified", "warning"),
    ]
    assert [item.request_id for item in silent] == ["Q2", "Q3", "Q4"]
    # 本文の不足の節・要求の文・渡した gaps の文のどれかで示していれば warning。
    section = "期限は 30 日です。\n\n資料からは確認できない点\n\n・例外"
    assert {item.severity for item in check_requests(section, requests[1:3])} == {"warning"}
    named = check_requests("例外の扱いは資料にありません。", requests[1:2])
    assert _codes(named) == [("request_missing", "warning")]
    gap = check_requests("申請先の窓口は不明です。", requests[2:3], ["申請先の窓口は不明"])
    assert _codes(gap) == [("request_partial", "warning")]
    assert check_requests("答え", []) == []


def test_excluded_steps_follow_known_branch_conditions() -> None:
    guide = _guide()
    # 条件が分からなければ、どの分岐も除かない。
    assert excluded_steps(guide, {}) == set()
    # 個別なら、グループの分岐の手順とそれだけに依存する手順を除く（確認は利用者の分岐にも依存）。
    assert excluded_steps(guide, {"target": "個別"}) == {"group", "group_save"}
    assert excluded_steps(guide, {"target": " ｸﾞﾙｰﾌﾟ "}) == {"user"}
    # どの分岐にも当たらない値では分岐を決められないので、除かない（#1320）。
    assert excluded_steps(guide, {"target": "全員"}) == set()


def test_check_guide_steps_order_branch_and_omissions() -> None:
    guide = _guide()
    ordered = "1. 権限タブを開く\n2. 利用者を選ぶ\n3. 付与結果を確認する"
    assert check_guide_steps(ordered, guide, {"target": "個別"}) == []

    reversed_order = "1. 利用者を選ぶ\n2. 権限タブを開く\n3. 付与結果を確認する"
    findings = check_guide_steps(reversed_order, guide, {"target": "個別"})
    assert _codes(findings) == [("step_order", "error")]
    assert (findings[0].step_id, findings[0].related_step_id) == ("user", "open")

    wrong = "1. 権限タブを開く\n2. グループを選ぶ\n3. 付与結果を確認する"
    findings = check_guide_steps(wrong, guide, {"target": "個別"})
    assert ("step_wrong_branch", "error") in _codes(findings)
    assert [item.step_id for item in findings if item.code == "step_wrong_branch"] == ["group"]

    # 前の手順・後の手順の欠落は warning。
    missing = check_guide_steps("利用者を選ぶ", guide, {"target": "個別"})
    assert _codes(missing) == [
        ("step_dependency_missing", "warning"),
        ("step_following_missing", "warning"),
    ]
    assert [item.step_id for item in missing] == ["user", "verify"]

    unmatched = check_guide_steps("設定画面で操作してください。", guide, {})
    assert _codes(unmatched) == [("guide_steps_unmatched", "warning")]
    assert check_guide_steps("何か", _guide(steps=[], branches=[]), {}) == []


def test_check_impact_requires_scope_and_approval() -> None:
    assert check_impact("権限タブを開く", _guide()) == []
    group = _guide(impact={"scope": "group", "approval_required": True})
    assert _codes(check_impact("権限タブを開く", group)) == [
        ("impact_scope_missing", "error"),
        ("approval_missing", "error"),
    ]
    assert check_impact("グループ全員に効きます。部門長の承認を得てから行います。", group) == []
    everyone = _guide(impact={"scope": "all"})
    assert _codes(check_impact("権限タブを開く", everyone)) == [("impact_scope_missing", "error")]
    assert check_impact("すべての利用者に反映されます。", everyone) == []


def _access_guide() -> SupportGuideContent:
    """評価セットの業務ガイド「アクセス権限の付与」（承認はグループに付与する分岐だけ）。"""
    data = json.loads(SUPPORT_GUIDES.read_text(encoding="utf-8"))
    payload = next(item for item in data["guides"] if item["title"] == "アクセス権限の付与")
    return SupportGuideContent.model_validate(payload)


# #1317 の実環境の D（run3）の da-trial-account-setup の回答（個別の利用者の分岐で答えた）。
_TRIAL_SETUP_ANSWER = """**検証用アカウントの登録 → アクセス権限付与 → 通知設定の手順**

1. **検証用アカウントを登録**
   - 管理画面で「利用者」メニューを開き「追加」ボタンを押す。
   - 「利用者種別」から **「検証用」** を選択。
   - 有効期限（最長30日）を入力し、保存する。

2. **アクセス権限を付与**
   - 登録した検証用アカウントの詳細画面を開き、タブから **「権限」** を選択。
   - 必要な権限をチェックし、「付与」ボタンを押す。

3. **通知を設定**
   - 同じ詳細画面の **「通知」** タブを開く。
   - 通知先メールアドレスを入力して保存する。
   - 「テスト送信」ボタンを押し、テストメールが届くことを確認する。"""


def test_check_impact_skips_the_approval_of_a_branch_that_does_not_apply() -> None:
    guide = _access_guide()
    assert guide.impact.steps == ["group-approval", "group"]
    # 個別の利用者の分岐で答えた回答に、グループへの付与の影響範囲・承認を求めない（#1320）。
    assert impact_applies(guide, {"target": "個別"}) is False
    assert check_impact(_TRIAL_SETUP_ANSWER, guide, {"target": "個別"}) == []
    # グループの分岐なら、今までどおり影響範囲と承認を求める。
    assert impact_applies(guide, {"target": "グループ"}) is True
    assert _codes(check_impact(_TRIAL_SETUP_ANSWER, guide, {"target": "グループ"})) == [
        ("impact_scope_missing", "error"),
        ("approval_missing", "error"),
    ]


def test_check_impact_stays_strict_when_the_branch_is_unknown() -> None:
    guide = _access_guide()
    # 付与先が分からない（分岐を決められない）ときは、今までどおり一律に確かめる（安全側）。
    for conditions in ({}, None, {"target": "不明な値"}, {"other": "個別"}):
        assert _codes(check_impact(_TRIAL_SETUP_ANSWER, guide, conditions)) == [
            ("impact_scope_missing", "error"),
            ("approval_missing", "error"),
        ], conditions
    # 係る手順を決めていない業務ガイドは、条件が分かっていてもすべての場合に確かめる。
    whole = _guide(impact={"scope": "group", "approval_required": True})
    assert impact_applies(whole, {"target": "個別"}) is True
    assert _codes(check_impact("権限タブを開く", whole, {"target": "個別"})) == [
        ("impact_scope_missing", "error"),
        ("approval_missing", "error"),
    ]
    # 係る手順の一部でも当たる分岐に残れば確かめる（共通の手順に係るとき）。
    shared = _guide(
        impact={"scope": "group", "approval_required": True, "steps": ["group", "verify"]}
    )
    assert impact_applies(shared, {"target": "個別"}) is True


class _GuideOracle(_Oracle):
    def __init__(self, profiles: set[str]) -> None:
        super().__init__()
        self.profiles = profiles

    async def get_search_answer_profile(self, profile_id: str) -> Any:
        return type("View", (), {"id": profile_id})() if profile_id in self.profiles else None


def _supported(monkeypatch: MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_claims(
        question: str, answer: str, items: list[dict[str, Any]], settings: Any
    ) -> dict[str, Any]:
        calls.append(answer)
        return {
            "status": "completed",
            "counts": {"supported": 1},
            "claim_checks": [
                {"answer_quote": answer, "status": "supported", "source_id": "c1", "reason": "記載"}
            ],
        }

    import rag_engine.evaluation.answer_validation as engine_validation

    monkeypatch.setattr(engine_validation, "validate_answer_claims", fake_claims)
    return calls


def _published(monkeypatch: MonkeyPatch, revision: int = 2) -> list[str]:
    seen: list[str] = []

    async def published_contents(self: Any, profile_id: str) -> list[Any]:
        seen.append(profile_id)
        return [("guide-1", revision, _guide(impact={"scope": "group"}))]

    monkeypatch.setattr(
        support_guide_store.SupportGuideStore, "published_contents", published_contents
    )
    return seen


async def test_validate_answer_runs_deterministic_checks(monkeypatch: MonkeyPatch) -> None:
    calls = _supported(monkeypatch)
    seen = _published(monkeypatch)
    oracle = cast(Any, _GuideOracle({"sap-1"}))
    refs = [EvidenceRef("d1", "c1")]
    good = (
        "1. 権限タブを開く\n2. グループを選ぶ\n3. グループの設定を保存する\n4. 付与結果を確認する"
    )
    result = await validate_answer(
        "q",
        good,
        refs,
        get_settings(),
        oracle=oracle,
        requests=[{"id": "Q1", "text": "手順", "status": "addressed"}],
        guide=GuideCheckRef("sap-1", "guide-1", 2, {"target": "グループ"}),
    )
    assert (result.valid, result.findings) == (True, [])
    assert result.checks == ["requests", "guide", "guide_steps", "impact"]
    assert result.guide_revision == 2
    assert seen == ["sap-1"]

    # 主張が裏付けられていても、error の finding があれば valid にしない。
    bad = "1. 権限タブを開く\n2. 利用者を選ぶ"
    result = await validate_answer(
        "q",
        bad,
        refs,
        get_settings(),
        oracle=oracle,
        requests=[{"id": "Q2", "text": "確認方法", "status": "missing"}],
        guide=GuideCheckRef("sap-1", "guide-1", 1, {"target": "グループ"}),
    )
    assert result.status == "completed" and result.counts == {"supported": 1}
    assert result.valid is False
    codes = {item.code for item in result.findings if item.severity == "error"}
    assert codes == {
        "request_missing",
        "guide_revision_stale",
        "step_wrong_branch",
        "impact_scope_missing",
    }
    assert len(calls) == 2

    # 根拠が読めなくても、決定的な検査の結果は返す。
    unreadable = await validate_answer(
        "q",
        bad,
        [EvidenceRef("d9", "x")],
        get_settings(),
        oracle=oracle,
        requests=[{"id": "Q2", "text": "確認方法", "status": "missing"}],
    )
    assert unreadable.status == "no_evidence"
    assert [item.code for item in unreadable.findings] == ["request_missing"]


async def test_validate_answer_guide_unavailable_and_profile_scope(
    monkeypatch: MonkeyPatch,
) -> None:
    calls = _supported(monkeypatch)
    _published(monkeypatch)
    oracle = cast(Any, _GuideOracle({"sap-1"}))
    refs = [EvidenceRef("d1", "c1")]
    archived = await validate_answer(
        "q",
        "権限タブを開く",
        refs,
        get_settings(),
        oracle=oracle,
        guide=GuideCheckRef("sap-1", "guide-gone", 1),
    )
    assert archived.valid is False
    assert [item.code for item in archived.findings] == ["guide_unavailable"]
    assert (archived.checks, archived.guide_revision) == (["guide"], None)
    # 利用できない検索・回答プロファイルは、モデルを呼ぶ前に止める。
    before = len(calls)
    with pytest.raises(GuideProfileNotFoundError):
        await validate_answer(
            "q",
            "a",
            refs,
            get_settings(),
            oracle=oracle,
            guide=GuideCheckRef("sap-other", "guide-1", 1),
        )
    assert len(calls) == before


@pytest.mark.usefixtures("auth")
def test_mcp_validate_answer_passes_requests_and_guide(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_validate(
        question: str, answer: str, refs: list[EvidenceRef], settings: Any, **kwargs: Any
    ) -> AnswerValidation:
        captured.update(kwargs)
        if kwargs["guide"].search_answer_profile_id == "sap-other":
            raise GuideProfileNotFoundError("sap-other")
        return AnswerValidation(
            status="completed",
            valid=False,
            counts={"supported": 1},
            checks=["requests", "guide", "guide_steps", "impact"],
            findings=[
                AnswerFinding(
                    "guide_steps",
                    "step_order",
                    "error",
                    "順序が逆です。",
                    step_id="user",
                    related_step_id="open",
                )
            ],
            guide_revision=3,
        )

    monkeypatch.setattr(mcp_tools, "validate_answer", fake_validate)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    arguments: dict[str, Any] = {
        "query": "q",
        "answer": "a",
        "evidence": [{"document_id": "d1", "chunk_id": "c1"}],
        "requests": [{"id": "Q1", "text": "手順", "status": "missing"}],
        "gaps": ["確認方法"],
        "guide": {
            "search_answer_profile_id": "sap-1",
            "guide_id": "guide-1",
            "revision": 3,
            "conditions": {"target": "個別"},
        },
    }
    body = _call("rag_validate_answer", arguments, _token(user.user_uuid))["structuredContent"]
    assert captured["requests"] == [{"id": "Q1", "text": "手順", "status": "missing"}]
    assert captured["gaps"] == ["確認方法"]
    assert captured["guide"] == GuideCheckRef("sap-1", "guide-1", 3, {"target": "個別"})
    assert body["valid"] is False
    assert body["checks"] == ["requests", "guide", "guide_steps", "impact"]
    assert body["guide_revision"] == 3
    assert body["findings"] == [
        {
            "check": "guide_steps",
            "code": "step_order",
            "severity": "error",
            "message": "順序が逆です。",
            "request_id": None,
            "step_id": "user",
            "related_step_id": "open",
        }
    ]
    # 範囲の外の検索・回答プロファイルは、業務ガイドが無いのと区別できるエラーにする。
    arguments["guide"]["search_answer_profile_id"] = "sap-other"
    denied = _call("rag_validate_answer", arguments, _token(user.user_uuid))
    assert denied["isError"] is True
    invalid = _call(
        "rag_validate_answer",
        {**arguments, "requests": [{"id": "Q1", "status": "done"}]},
        _token(user.user_uuid),
    )
    assert invalid["isError"] is True
