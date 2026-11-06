"""画像の用途の設定と、過去の解析版に記録された用途の読み取り。"""

from collections.abc import Mapping
from typing import Literal

ImagePurpose = Literal["record", "example"]
RecordedImagePurpose = Literal["record", "example", "unknown"]
DEFAULT_IMAGE_PURPOSE: ImagePurpose = "record"
IMAGE_PURPOSE_METADATA_KEY = "vision_image_purpose"


def recorded_image_purpose(metadata: Mapping[str, object] | None) -> RecordedImagePurpose:
    """未記録や不正な過去の値を、現在の既定値で補完しない。"""
    value = metadata.get(IMAGE_PURPOSE_METADATA_KEY) if metadata is not None else None
    if value == "record":
        return "record"
    if value == "example":
        return "example"
    return "unknown"
