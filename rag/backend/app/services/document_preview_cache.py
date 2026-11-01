"""版に結び付けた private なページ派生物の容量制限付きキャッシュ。"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

MAX_CACHE_BYTES = 128 * 1024 * 1024
CACHE_TTL_SECONDS = 3600


class DocumentPreviewCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, identity: str) -> Path:
        return self.root / (hashlib.sha256(identity.encode()).hexdigest() + ".cache")

    def get(self, identity: str) -> bytes | None:
        path = self.path(identity)
        try:
            if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                return None
            return path.read_bytes()
        except OSError:
            return None

    def put(self, identity: str, content: bytes) -> None:
        if len(content) > MAX_CACHE_BYTES:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged = self.root / f"{uuid4().hex}.tmp"
            descriptor = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            staged.replace(self.path(identity))
            files = sorted(
                self.root.glob("*.cache"),
                key=lambda path: (path == self.path(identity), path.stat().st_mtime),
                reverse=True,
            )
            total = 0
            for path in files:
                total += path.stat().st_size
                if (
                    total > MAX_CACHE_BYTES
                    or time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS
                ):
                    path.unlink(missing_ok=True)
        except OSError:
            # キャッシュは任意。保存失敗を文書の取得失敗へ変えない。
            return
