"""MinerU のライセンスの条件の確認（#3359）。

MinerU のコードの許可（MinerU Open Source License）は Apache-2.0 に追加の条件がある
（企業の規模による商用ライセンス・オンラインサービスでの利用の明示・違反時の自動の終了）。
管理者が「文書解析」画面で条件を確認するまで、MinerU を有効にも選択にもできず、取込でも
呼ばない。確認の記録（確認した人・日時）は parser-settings.json に保存する（DDL を持たない）。
"""

from __future__ import annotations

from typing import Any

MINERU_BACKEND = "mineru"
MINERU_REPOSITORY_URL = "https://github.com/opendatalab/MinerU"
MINERU_LICENSE_URL = "https://github.com/opendatalab/MinerU/blob/master/LICENSE.md"
MINERU_LICENSE_UNACKNOWLEDGED_REASON = "license_unacknowledged"
MINERU_LICENSE_UNACKNOWLEDGED_WARNING = "mineru_license_unacknowledged"
MINERU_LICENSE_REQUIRED_MESSAGE = (
    "MinerU を使う前に、管理者が「取込・検索・回答設定 › 文書解析」で MinerU のライセンスの"
    "条件を確認してください。"
)


def mineru_license_acknowledged(settings: Any) -> bool:
    """管理者が MinerU のライセンスの条件を確認済みか。"""
    return bool(str(getattr(settings, "rag_parser_mineru_license_acknowledged_at", "")).strip())


def settings_use_mineru(settings: Any) -> bool:
    """全体の設定で MinerU を使用エンジンにしているか、有効にしているか。"""
    backend = str(getattr(settings, "rag_parser_adapter_backend", "")).strip().casefold()
    return backend == MINERU_BACKEND or bool(getattr(settings, "rag_parser_mineru_enabled", False))
