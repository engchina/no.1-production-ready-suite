"""画像補正の変換テスト。

passthrough 経路は OpenCV 非依存で検証し、実補正は cv2 がある環境でのみ検証する。
"""

from __future__ import annotations

import pytest
from rag_parser_core.source import SourceModality, SourceProfile

from app.converters import convert


def _convert(source: bytes, content_type: str = "image/png", *, enhancer=None, profile=None):
    return convert(source, content_type, "image_enhance", profile, enhancer=enhancer)


def test_unsupported_profile_passthrough() -> None:
    outcome = convert(b"\x89PNG", "image/png", "passthrough", None)
    assert outcome.converted is False
    assert outcome.converter_name == "passthrough"


def test_empty_source_passthrough() -> None:
    outcome = _convert(b"")
    assert outcome.converted is False
    assert "image_empty" in outcome.warnings


def test_non_image_passthrough() -> None:
    outcome = _convert(b"hello", content_type="text/plain")
    assert outcome.converted is False
    assert "image_not_applicable" in outcome.warnings


def _image_profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="scan.tiff",
        sanitized_file_name="scan.tiff",
        content_type="",
        file_size_bytes=8,
        content_sha256="0" * 64,
        modality=SourceModality.IMAGE,
        parser_profile="auto",
    )


def test_image_modality_routes_even_without_content_type() -> None:
    # content-type が空でも source modality が image なら補正対象。enhancer 注入で検証。
    outcome = _convert(
        b"rawbytes", content_type="", enhancer=lambda b: b"PNGDATA", profile=_image_profile()
    )
    assert outcome.converted is True
    assert outcome.converter_name == "image_enhance"
    assert outcome.derived_content_type == "image/png"
    assert outcome.derived_bytes == b"PNGDATA"


def test_decode_failure_passthrough() -> None:
    # enhancer が None(復号不可)を返したら passthrough へ縮退。
    outcome = _convert(b"notanimage", enhancer=lambda b: None)
    assert outcome.converted is False
    assert "image_decode_failed" in outcome.warnings


def test_enhancer_exception_passthrough() -> None:
    def boom(_b: bytes) -> bytes | None:
        raise RuntimeError("cv error")

    outcome = _convert(b"x", enhancer=boom)
    assert outcome.converted is False
    assert "image_enhance_failed" in outcome.warnings


def test_real_opencv_enhance_roundtrip() -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from app.converters import _default_enhancer

    # 単色のテスト画像を PNG エンコードして補正を通す。
    image = np.full((40, 60, 3), 200, dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    derived = _default_enhancer(buffer.tobytes())
    assert derived is not None and len(derived) > 0
    # 出力が PNG として再復号できること。
    decoded = cv2.imdecode(np.frombuffer(derived, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    assert decoded.shape == (40, 60)


def test_real_opencv_decode_failure_returns_none() -> None:
    pytest.importorskip("cv2")
    from app.converters import _default_enhancer

    assert _default_enhancer(b"not a real image") is None
