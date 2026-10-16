"""アプリごとに選択する業務の語・問い合わせ規則。

顧客・業務に固有の語（業務名、用語の別名、外部連携先の名称）はパッケージに含めない。
`legacy` profile の中身は repo 外の JSON（`RAG_ENGINE_DOMAIN_PROFILE_FILE`、未指定時は作業ディレクトリの
`domain_profile.json`）から読み込む。書式は `domain_profile.example.json` を参照。
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DOMAIN_PROFILE_FILE_ENV = "RAG_ENGINE_DOMAIN_PROFILE_FILE"
DEFAULT_DOMAIN_PROFILE_FILE = "domain_profile.json"


@dataclass(frozen=True)
class DomainProfile:
    """業務の既定値。generic は既存業務の別名を推測しない。"""

    name: str = "generic"
    business_patterns: tuple[tuple[str, str], ...] = ()
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()
    japanese_inquiry_rules: bool = False
    prompt_overrides: tuple[tuple[str, str], ...] = ()
    # 問い合わせ条件の抽出で、汎用語に加えてファイル/データ確認・外部連携とみなす業務固有の語。
    file_data_terms: tuple[str, ...] = ()
    external_context_terms: tuple[str, ...] = ()
    # 操作手順の節ラベルの正規表現（空なら一般語の既定）。問い合わせ元を表す語（空なら一般語の既定）。#849
    operation_section_pattern: str = ""
    requester_terms: tuple[str, ...] = ()


def domain_profile_path() -> Path:
    """`legacy` profile の JSON の場所。環境変数が優先で、既定は作業ディレクトリ基準（`.env` と同じ）。"""
    return Path(os.environ.get(DOMAIN_PROFILE_FILE_ENV) or DEFAULT_DOMAIN_PROFILE_FILE)


@lru_cache(maxsize=2)
def load_profile(name: str = "generic") -> DomainProfile:
    """profile を読み込む。未知の名前は ValueError とする。

    `legacy` は日本語の問い合わせ規則を有効にした profile。業務データは `domain_profile_path()` の JSON から
    読み、ファイルがなければ別名なしで動く。結果は process 内で cache するため、ファイルや環境変数を
    変えた後は `load_profile.cache_clear()` が必要。JSON が壊れていれば json.JSONDecodeError を送出する。
    """
    if name == "generic":
        return DomainProfile()
    if name != "legacy":
        raise ValueError(f"Unknown domain profile: {name}")
    path = domain_profile_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return DomainProfile(
        name="legacy",
        japanese_inquiry_rules=True,
        business_patterns=tuple(tuple(pair) for pair in data.get("business_patterns", ())),
        aliases=tuple((key, tuple(values)) for key, values in data.get("aliases", ())),
        file_data_terms=tuple(data.get("file_data_terms", ())),
        external_context_terms=tuple(data.get("external_context_terms", ())),
        operation_section_pattern=str(data.get("operation_section_pattern", "") or ""),
        requester_terms=tuple(data.get("requester_terms", ())),
    )
