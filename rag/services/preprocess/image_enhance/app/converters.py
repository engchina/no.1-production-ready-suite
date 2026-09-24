"""画像補正(image_enhance)前処理マイクロサービスの変換実装。

スキャン画像・写真を OCR しやすい形へ補正する。グレースケール化 → ノイズ除去 →
CLAHE コントラスト均一化 → 軽い傾き補正(deskew)を決定論で行い、PNG(可逆)で返す。
後段の OCR parser(mineru / dots_ocr / glm_ocr / Enterprise AI VLM)の精度を前段で底上げする。
重い CV 依存(OpenCV)はこのサービスに隔離し、他 parser / backend に非干渉。

画像でない・空・復号失敗のときは passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

from collections.abc import Callable

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# enhancer: 画像 bytes -> 補正済み PNG bytes(None=補正不可)。差し替え可能にしてテストを
# OpenCV 非依存にする。
Enhancer = Callable[[bytes], bytes | None]

# 補正対象とみなす content-type の接頭辞。
_IMAGE_CONTENT_PREFIX = "image/"
# 傾き補正の上限角度(これを超える検出は誤検出とみなし補正しない)。
_MAX_DESKEW_DEGREES = 15.0
# 入力画像の長辺上限(超過時は縮小して CPU/メモリを抑える)。
_MAX_DIMENSION = 4000


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
    *,
    enhancer: Enhancer | None = None,
) -> ConvertOutcome:
    """選択プリセットで変換する。image_enhance 以外・非画像・失敗は passthrough へ縮退する。"""
    if preprocess_profile != "image_enhance":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _image_enhance(
        source_bytes, content_type, source_profile, enhancer=enhancer or _default_enhancer
    )


def _is_image(content_type: str, source_profile: SourceProfile | None) -> bool:
    """content-type / source modality から画像かどうかを判定する。"""
    if source_profile is not None and source_profile.modality == "image":
        return True
    return content_type.strip().lower().startswith(_IMAGE_CONTENT_PREFIX)


def _image_enhance(
    source_bytes: bytes,
    content_type: str,
    source_profile: SourceProfile | None,
    *,
    enhancer: Enhancer,
) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="image_empty")
    if not _is_image(content_type, source_profile):
        return ConvertOutcome.passthrough(reason="image_not_applicable")
    try:
        derived = enhancer(source_bytes)
    except Exception:  # noqa: BLE001 - 補正失敗は原本へ安全に縮退する境界
        return ConvertOutcome.passthrough(reason="image_enhance_failed")
    if not derived:
        return ConvertOutcome.passthrough(reason="image_decode_failed")
    return ConvertOutcome(
        converted=True,
        converter_name="image_enhance",
        converter_version="v1",
        derived_bytes=derived,
        derived_content_type="image/png",
    )


def _default_enhancer(source_bytes: bytes) -> bytes | None:
    """OpenCV で OCR 向け補正を行い PNG bytes を返す(復号不可は None)。"""
    import cv2
    import numpy as np

    array = np.frombuffer(source_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        return None
    image = _downscale(image, cv2)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrasted = clahe.apply(denoised)
    deskewed = _deskew(contrasted, cv2, np)
    ok, buffer = cv2.imencode(".png", deskewed)
    if not ok:
        return None
    return buffer.tobytes()


def _downscale(image: object, cv2: object) -> object:
    """長辺が上限を超える画像をアスペクト比維持で縮小する。"""
    height, width = image.shape[:2]  # type: ignore[attr-defined]
    longest = max(height, width)
    if longest <= _MAX_DIMENSION:
        return image
    scale = _MAX_DIMENSION / float(longest)
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)  # type: ignore[attr-defined]


def _deskew(gray: object, cv2: object, np: object) -> object:
    """テキスト画素の最小外接矩形から傾きを推定し、小角度だけ補正する。"""
    threshold = cv2.threshold(  # type: ignore[attr-defined]
        gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU  # type: ignore[attr-defined]
    )[1]
    coords = np.column_stack(np.where(threshold > 0))  # type: ignore[attr-defined]
    if coords.size == 0:  # type: ignore[attr-defined]
        return gray
    angle = cv2.minAreaRect(coords)[-1]  # type: ignore[attr-defined]
    if angle < -45:
        angle = 90 + angle
    correction = -angle
    if abs(correction) > _MAX_DESKEW_DEGREES:
        # 大きすぎる検出は誤検出とみなし補正しない(画像を壊さない)。
        return gray
    height, width = gray.shape[:2]  # type: ignore[attr-defined]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, correction, 1.0)  # type: ignore[attr-defined]
    return cv2.warpAffine(  # type: ignore[attr-defined]
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,  # type: ignore[attr-defined]
        borderMode=cv2.BORDER_REPLICATE,  # type: ignore[attr-defined]
    )
