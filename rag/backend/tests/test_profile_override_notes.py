"""設定の説明が「検索・回答プロファイルで上書き可」と実装のずれを起こさないようにする(#3516)。

検索・回答プロファイルが上書きできる設定は、``kb_adapter_config._QUERY_FIELD_MAP`` の欄だけ(#4784)。
それ以外の設定は全体設定だけで決める。その説明が次のどこでもプロファイルでの上書き・選択を
案内していないことを、``Settings`` の項目から引いて確かめる(項目を手で並べない)。

- ``backend/.env.example`` の、その環境変数の直前のコメント
- ``app/config.py`` の ``Settings`` の項目の ``description``
- ``rag/docs/rag-engine.md`` の、その環境変数の表の行
- 画面の文言(``frontend/src/lib/i18n.ts``。全体設定の画面 ``settings.answering.*`` と、
  その項目の名前を「」で挙げる文)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pr_backend_core.config.env import settings_env_name

from app.config import Settings
from app.rag import kb_adapter_config

BACKEND = Path(__file__).resolve().parents[1]
RAG = BACKEND.parent
ENV_EXAMPLE = BACKEND / ".env.example"
RAG_ENGINE_DOC = RAG / "docs" / "rag-engine.md"
I18N = RAG / "frontend" / "src" / "lib" / "i18n.ts"

# 「検索・回答プロファイルで上書きできる / 選ぶ / 変える / 決める」の案内。「上書きしない」
# 「上書きできない」は案内ではない(全体設定だけで決める、という正しい説明)。
_OVERRIDE_CLAIM = re.compile(
    r"プロファイル[^。]{0,30}?"
    r"(?:上書き(?!し(?:ない|ません))(?!でき(?:ない|ません))|で選|で変え|で決め)"
)


def _claims_profile_override(text: str) -> list[str]:
    """文(``。`` 区切り)のうち、プロファイルでの上書き・選択を案内しているものを返す。"""
    flat = re.sub(r"\s+", "", text)
    return [sentence for sentence in flat.split("。") if _OVERRIDE_CLAIM.search(sentence)]


# 検査が空振りしていないことを確かめる下限(見つけた説明の数)。
_MIN_CHECKED = 7

# プロファイルが要求ごとに解決して入れる内部の項目(.env で決める設定ではない)。
_PROFILE_RESOLVED_FIELDS = {"rag_language_pack_overrides", "rag_answer_contact_override"}


def _global_settings_fields() -> list[str]:
    """全体設定だけで決める項目(プロファイルの query の対応表に無い Settings の項目)。"""
    overridable = set(kb_adapter_config._QUERY_FIELD_MAP.values()) | _PROFILE_RESOLVED_FIELDS
    return sorted(set(Settings.model_fields) - overridable)


def _env_comment_blocks() -> dict[str, str]:
    """``.env.example`` の ``NAME=value`` ごとに、直前の連続したコメント行をつないだ文を返す。"""
    blocks: dict[str, str] = {}
    comment: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            comment.append(line.removeprefix("#").strip())
            continue
        name, separator, _ = line.partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            blocks[name] = "".join(comment)
        comment = []
    return blocks


def _doc_rows() -> dict[str, str]:
    """``rag-engine.md`` の表の行を、先頭の列の環境変数名(`` `RAG_X` ``)ごとに返す。"""
    rows: dict[str, str] = {}
    for line in RAG_ENGINE_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1]
        for name in re.findall(r"`(RAG_[A-Z0-9_]+)`", first_cell):
            rows[name] = line
    return rows


def _i18n_entries() -> dict[str, str]:
    text = I18N.read_text(encoding="utf-8")
    pattern = re.compile(r'"([\w.\-]+)":\s*"((?:[^"\\]|\\.)*)"')
    return {key: value for key, value in pattern.findall(text)}


def test_checker_detects_override_claims_and_accepts_global_only_notes() -> None:
    """検査そのもの: 上書きの案内は拾い、「上書きしない」の説明は拾わない。"""
    assert _claims_profile_override("検索・回答プロファイルで上書き可。")
    assert _claims_profile_override("検索・回答プロファイルで上書きできる。")
    assert _claims_profile_override("検索・回答プロファイルで選ぶ(環境変数は持たない)。")
    assert _claims_profile_override(
        "検索方法と検索・回答プロファイルの graph_expansion_enabled で選ぶ。"
    )
    assert not _claims_profile_override(
        "全体設定だけで決め、検索・回答プロファイルでは上書きしない。"
    )
    assert not _claims_profile_override("検索・回答プロファイルでは上書きできません。")
    assert not _claims_profile_override(
        "項目の定義が無い検索・回答プロファイルでは LLM を呼ばない。"
    )


def test_env_example_does_not_claim_profile_override_for_global_only_fields() -> None:
    blocks = _env_comment_blocks()
    checked = 0
    for field in _global_settings_fields():
        env_name = settings_env_name("RAG_", field)
        note = blocks.get(env_name)
        if note is None:
            continue
        checked += 1
        assert _claims_profile_override(note) == [], (
            f"{env_name} は全体設定だけの項目。"
            ".env.example のコメントが検索・回答プロファイルでの上書きを案内している"
        )
    assert checked >= _MIN_CHECKED


@pytest.mark.parametrize("field", _global_settings_fields())
def test_config_description_does_not_claim_profile_override_for_global_only_fields(
    field: str,
) -> None:
    description = Settings.model_fields[field].description or ""
    assert _claims_profile_override(description) == [], (
        f"{field} は全体設定だけの項目。"
        "Settings の description が検索・回答プロファイルでの上書きを案内している"
    )


def test_engine_doc_does_not_claim_profile_override_for_global_only_fields() -> None:
    """表の行がある項目だけ見る(行が無い項目は別の docs で説明している)。"""
    rows = _doc_rows()
    checked = 0
    for field in _global_settings_fields():
        env_name = settings_env_name("RAG_", field)
        row = rows.get(env_name)
        if row is None:
            continue
        checked += 1
        assert _claims_profile_override(row) == [], (
            f"{env_name} は全体設定だけの項目。"
            "rag/docs/rag-engine.md の行が検索・回答プロファイルでの上書きを案内している"
        )
    assert checked >= _MIN_CHECKED


def test_screen_text_does_not_claim_profile_override_for_global_only_fields() -> None:
    """全体設定の画面の文言と、その項目の名前を挙げる文が、プロファイルでの上書きを案内しない。"""
    entries = _i18n_entries()
    # 全体設定の画面(settings.answering.*)の項目の名前: 説明(…Hint)を持つ項目のラベル。
    labels = {
        entries[key]
        for key in entries
        if key.startswith("settings.answering.") and f"{key}Hint" in entries
    }
    assert len(labels) >= _MIN_CHECKED, labels
    offenders: list[str] = []
    for key, value in entries.items():
        in_answering_screen = key.startswith("settings.answering.")
        for sentence in _claims_profile_override(value):
            if in_answering_screen or any(f"「{label}」" in sentence for label in labels):
                offenders.append(f"{key}: {sentence}")
    assert offenders == []
