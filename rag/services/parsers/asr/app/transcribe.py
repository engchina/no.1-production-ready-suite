"""ローカル faster-whisper による音声文字起こし。

音声/動画バイト列を faster-whisper(CTranslate2、GPU)で転写し、共有 remap
(`rag_parser_core.asr.build_transcript_extraction`)で `StructuredExtraction` を作る。
重い依存(faster-whisper / torch)はこのサービスに隔離し、他 parser / backend に非干渉。

transcriber は差し替え可能にして、テストをモデル非依存にする。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable

from rag_parser_core.asr import TranscriptSegment

# transcriber: 音声ファイルパス -> (全文, segments, 言語コード)。
Transcriber = Callable[[str], tuple[str, list[TranscriptSegment], str | None]]

# 既定モデル(GPU メモリと精度のバランス。GLM/dots と同じく env で上書き可)。
_DEFAULT_MODEL = os.environ.get("ASR_MODEL_SIZE", "large-v3")
_DEFAULT_DEVICE = os.environ.get("ASR_DEVICE", "cuda")
_DEFAULT_COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "float16")


def transcribe(
    audio_bytes: bytes,
    *,
    suffix: str = ".bin",
    transcriber: Transcriber | None = None,
) -> tuple[str, list[TranscriptSegment], str | None]:
    """音声バイト列を転写して (全文, segments, 言語) を返す。"""
    run = transcriber or _default_transcriber
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(audio_bytes)
        path = handle.name
    try:
        return run(path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


_model_cache: object | None = None


def _default_transcriber(path: str) -> tuple[str, list[TranscriptSegment], str | None]:
    """faster-whisper で転写する(モデルはプロセス内でキャッシュ)。"""
    from faster_whisper import WhisperModel

    global _model_cache
    if _model_cache is None:
        _model_cache = WhisperModel(
            _DEFAULT_MODEL, device=_DEFAULT_DEVICE, compute_type=_DEFAULT_COMPUTE_TYPE
        )
    segments_iter, info = _model_cache.transcribe(path, vad_filter=True)  # type: ignore[attr-defined]
    segments: list[TranscriptSegment] = []
    parts: list[str] = []
    for segment in segments_iter:
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start=getattr(segment, "start", None),
                end=getattr(segment, "end", None),
            )
        )
        parts.append(text)
    language = getattr(info, "language", None)
    return "\n".join(parts), segments, language
