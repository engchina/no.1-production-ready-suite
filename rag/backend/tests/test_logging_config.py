"""ロギング設定のテスト。"""

import logging

from app.logging_config import _NOISY_LOGGERS, configure_logging


def test_configure_logging_sets_root_level() -> None:
    """ルートロガーのレベルが指定どおりに設定される。"""
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    # 後続テストへ影響しないよう既定へ戻す。
    configure_logging("INFO")


def test_configure_logging_quiets_pdfminer_warnings() -> None:
    """pdfminer の無害な警告ログが抑制される。"""
    # 事前に WARNING に下げても configure_logging 後は ERROR に引き上げられる。
    logging.getLogger("pdfminer.pdffont").setLevel(logging.WARNING)

    configure_logging("INFO")

    for name, level in _NOISY_LOGGERS.items():
        logger = logging.getLogger(name)
        assert logger.level == level
        # FontBBox 警告(WARNING)は実際に無効化される。
        assert not logger.isEnabledFor(logging.WARNING)
