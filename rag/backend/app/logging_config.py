"""JSON 構造化ロギング設定。"""

import logging

from pythonjsonlogger import json as jsonlogger

# PDF パース(pdfminer / pdfplumber 経由)が出す無害な警告でログが溢れるため抑制する。
# 例: "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats"
# これらはフォント記述子の欠損に対する fallback で、抽出結果には影響しない。
_NOISY_LOGGERS: dict[str, int] = {
    "pdfminer": logging.ERROR,
    "pdfminer.pdffont": logging.ERROR,
    "pdfminer.pdfinterp": logging.ERROR,
}


def _quiet_noisy_loggers() -> None:
    """サードパーティの過剰な警告ログのレベルを引き上げる。"""
    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


def configure_logging(level: str = "INFO") -> None:
    """ルートロガーを JSON 形式で構成する。"""
    handler = logging.StreamHandler()
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _quiet_noisy_loggers()
