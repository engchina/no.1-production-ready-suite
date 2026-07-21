"""pytest 共通設定。

ローカル `.env` は実 Oracle smoke 用に `NL2SQL_RUNTIME_MODE=oracle` へ切り替えられる。
単体テストは CI と同じ deterministic/memory 実行に固定し、開発者環境の `.env` に依存させない。
"""

from __future__ import annotations

import os

os.environ["ENABLE_METRICS"] = "false"
os.environ["DEBUG"] = "false"
os.environ["NL2SQL_RUNTIME_MODE"] = "deterministic"
os.environ["NL2SQL_PERSISTENCE_MODE"] = "memory"
os.environ["NL2SQL_SELECT_AI_CREDENTIAL_NAME"] = ""
os.environ["APP_AUTH_ENABLED"] = "false"
