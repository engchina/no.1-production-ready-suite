"""Run・イベントの作成日時（`runtime._now`）の時計を戻すテストの道具（#5150）。

OS の時計（wall clock）は時刻の同期で後ろへ戻ることがある。`runtime_clock` の fixture（conftest）が
`runtime._now` の見る時計を差し替え、テストが `rewind` で時計を戻す。
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

import pytest

from app.features.agent import runtime as agent_runtime


class RuntimeClock:
    """`runtime._now` の時計（OS の時計から `rewind` で戻した分を引く）。"""

    def __init__(self) -> None:
        self.offset = timedelta()

    def rewind(self, seconds: float) -> None:
        """時計を `seconds` 秒戻す（時刻の同期で OS の時計が後ろへ戻ったとき）。"""
        self.offset += timedelta(seconds=seconds)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = self

        class _RewoundDatetime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
                return datetime.now(tz) - clock.offset

        monkeypatch.setattr(agent_runtime, "datetime", _RewoundDatetime)
