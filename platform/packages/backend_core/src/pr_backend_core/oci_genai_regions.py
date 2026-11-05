"""Generative AI の呼出先。認証リージョンとは別に決める。"""

from collections.abc import Mapping
from typing import Any, Literal

GenAiRegion = Literal["ap-tokyo-1", "ap-osaka-1", "us-chicago-1"]
DEFAULT_GENAI_REGION: GenAiRegion = "ap-osaka-1"
GENAI_REGIONS = ("ap-tokyo-1", "ap-osaka-1", "us-chicago-1")


def genai_endpoint(region: str) -> str:
    """選択したリージョンのSDK endpointを返す。認証設定へ戻さない。"""
    if region not in GENAI_REGIONS:
        raise ValueError("Generative AI のリージョンがサポートされていません。")
    return f"https://inference.generativeai.{region}.oci.oraclecloud.com"


def genai_config(config: Mapping[str, Any], region: str) -> dict[str, Any]:
    """資格情報を保ち、呼出先リージョンだけを一時的に設定する。"""
    genai_endpoint(region)
    return {**config, "region": region}
