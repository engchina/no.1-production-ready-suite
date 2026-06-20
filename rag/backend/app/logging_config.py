"""JSON 構造化ロギング設定。

実装は共有 backend インフラ `pr_backend_core` に移管。RAG 固有のノイズロガー抑制
（pdfminer のフォント警告等）をここで注入する。
"""

import logging

from pr_backend_core import configure_logging as _configure_logging

# PDF パース(pdfminer / pdfplumber 経由)が出す無害な警告でログが溢れるため抑制する。
# 例: "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats"
# これらはフォント記述子の欠損に対する fallback で、抽出結果には影響しない。
_NOISY_LOGGERS: dict[str, int] = {
    "pdfminer": logging.ERROR,
    "pdfminer.pdffont": logging.ERROR,
    "pdfminer.pdfinterp": logging.ERROR,
}


def configure_logging(level: str = "INFO") -> None:
    """ルートロガーを JSON 形式で構成する（共有実装 + RAG 固有のノイズ抑制）。"""
    _configure_logging(level, quiet_loggers=_NOISY_LOGGERS)
