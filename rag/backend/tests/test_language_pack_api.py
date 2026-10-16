"""言語資源の画面の API(#2181): 取得・保存(組み込みとの差だけ)・形の誤り・試す・解析への反映。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rag_parser_core.language import builtin_pack, current_pack

from app.config import get_settings, resolve_language_pack_file
from app.main import app
from app.rag.cross_references import extract_references
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_get_returns_builtin_fields_and_effective_values() -> None:
    data = client.get("/api/settings/language-pack").json()["data"]
    assert data["overrides"] == {}
    assert data["effective"] == data["builtin"]
    assert data["hash"] == builtin_pack().hash
    kinds = {(f["section"], f["key"]): f["kind"] for f in data["fields"]}
    assert kinds[("numbers", "kanji_digits")] == "char_values"
    assert kinds[("symbols", "normalization")] == "choice"
    assert "附則" in data["builtin"]["references"]["part_prefixes"]


def test_put_saves_only_differences_and_changes_extraction() -> None:
    assert [s.kind for s in extract_references("第二款を参照")] != ["article"]
    overrides = {"numbers": {"article_units": ["条", " 款 ", "款"]}}

    resp = client.put("/api/settings/language-pack", json={"overrides": overrides})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["overrides"] == {"numbers": {"article_units": ["条", "款"]}}
    saved = json.loads(Path(resolve_language_pack_file(get_settings())).read_text("utf-8"))
    assert saved == {"overrides": {"numbers": {"article_units": ["条", "款"]}}}
    assert current_pack().hash == data["hash"]
    assert [(s.kind, s.key) for s in extract_references("第二款を参照")] == [("article", "2")]

    # 空の上書きで組み込みに戻る。
    reset = client.put("/api/settings/language-pack", json={"overrides": {}}).json()["data"]
    assert reset["overrides"] == {}
    assert current_pack().hash == builtin_pack().hash


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("symbols", "bracket_pairs", ["("]),
        ("english", "en_section_words", ["part=volume"]),
        ("symbols", "normalization", "NFKD"),
        ("numbers", "kanji_digits", ["一=いち"]),
        ("numbers", "kanji_multipliers", ["十十=10"]),
        ("symbols", "name_chars", ["あいう"]),
        ("references", "separate_part_patterns", ["改正"]),
        ("references", "abbreviation_targets", ["法"]),
        ("numbers", "unknown_key", ["x"]),
    ],
    ids=[
        "bracket-not-pair",
        "en-kind-unknown",
        "normalization-unknown",
        "kanji-not-number",
        "multiplier-not-one-char",
        "name-char-not-range",
        "pattern-without-number",
        "map-without-value",
        "unknown-field",
    ],
)
def test_put_rejects_malformed_values(section: str, key: str, value: object) -> None:
    resp = client.put("/api/settings/language-pack", json={"overrides": {section: {key: value}}})
    assert resp.status_code == 422
    assert f"{section}.{key}" in json.dumps(resp.json(), ensure_ascii=False)
    assert current_pack().hash == builtin_pack().hash


def test_reload_reads_other_worker_save_and_ignores_broken_file() -> None:
    path = Path(resolve_language_pack_file(get_settings()))
    path.write_text(json.dumps({"overrides": {"numbers": {"article_units": ["款"]}}}), "utf-8")
    get_settings()
    assert current_pack().words("numbers", "article_units") == ("款",)
    path.write_text("{broken", "utf-8")
    get_settings()
    assert current_pack().hash == builtin_pack().hash


def test_preview_compares_saved_and_draft() -> None:
    resp = client.post(
        "/api/settings/language-pack/preview",
        json={
            "text": "第二款 届出\n第二款を参照",
            "overrides": {"numbers": {"article_units": ["条", "款"]}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [r["kind"] for r in data["draft"]["references"]] == ["article"]
    assert "article:2" in data["draft"]["heading_keys"]
    assert "article:2" not in data["saved"]["heading_keys"]
    # 試すだけで保存しない。
    assert current_pack().hash == builtin_pack().hash


def test_every_field_has_screen_label_and_help() -> None:
    """言語資源の項目は、画面の名前と説明(frontend の i18n)を持つ(#2181)。"""
    import re

    from rag_parser_core.language import FIELD_SPECS, SECTIONS

    i18n = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "i18n.ts").read_text(
        encoding="utf-8"
    )
    keys = set(re.findall(r'^\s*"(settings\.language\.[^"]+)":', i18n, re.MULTILINE))
    missing = [
        key
        for spec in FIELD_SPECS
        for key in (
            f"settings.language.field.{spec.key}",
            f"settings.language.field.{spec.key}.help",
        )
        if key not in keys
    ]
    missing += [
        key
        for section in SECTIONS
        for key in (
            f"settings.language.section.{section}",
            f"settings.language.section.{section}.help",
        )
        if key not in keys
    ]
    assert missing == []


def test_get_reports_documents_outside_current_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """今の言語資源の取込の項目で分割していない文書の数を出す(#2181)。

    DB に届かなければ数を出さない。
    """
    from app.clients.oracle import OracleClient

    async def count(self: OracleClient, pack_hash: str) -> int:
        assert pack_hash == current_pack().ingest_hash
        return 7

    monkeypatch.setattr(OracleClient, "count_documents_outside_language_pack", count)
    assert client.get("/api/settings/language-pack").json()["data"]["outdated_documents"] == 7

    async def broken(self: OracleClient, pack_hash: str) -> int:
        raise RuntimeError("ORA-12541")

    monkeypatch.setattr(OracleClient, "count_documents_outside_language_pack", broken)
    assert client.get("/api/settings/language-pack").json()["data"]["outdated_documents"] is None


def test_history_lists_saved_versions_and_restores_one() -> None:
    """保存ごとに版を残し、前の版に戻せる。戻したことも新しい版として残す(#2181 段 6)。"""
    first = {"numbers": {"article_units": ["条", "款"]}}
    client.put("/api/settings/language-pack", json={"overrides": first})
    client.put("/api/settings/language-pack", json={"overrides": {}})

    versions = client.get("/api/settings/language-pack/history").json()["data"]["versions"]
    assert [v["changed_fields"] for v in versions[:2]] == [0, 1]
    older = versions[1]
    assert older["hash"] != versions[0]["hash"]

    restored = client.post(f"/api/settings/language-pack/history/{older['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["data"]["overrides"] == first
    latest = client.get("/api/settings/language-pack/history").json()["data"]["versions"][0]
    assert latest["restored_from"] == older["id"]
    assert latest["changed_fields"] == 1

    missing = client.post("/api/settings/language-pack/history/0000/restore")
    assert missing.status_code == 404
    client.put("/api/settings/language-pack", json={"overrides": {}})
