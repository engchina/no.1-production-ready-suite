"""認証リージョンとモデルごとの呼出先を分離する契約。"""

import pytest

from pr_backend_core.oci_genai_regions import DEFAULT_GENAI_REGION, genai_config, genai_endpoint


@pytest.mark.parametrize("region", ["ap-tokyo-1", "ap-osaka-1", "us-chicago-1"])
def test_region_overrides_endpoint_without_mutating_authentication(region: str) -> None:
    original = {
        "region": "eu-frankfurt-1",
        "user": "synthetic-user",
        "key_content": "synthetic-key",
    }
    selected = genai_config(original, region)
    assert selected["region"] == region
    assert selected["user"] == original["user"]
    assert selected["key_content"] == original["key_content"]
    assert original["region"] == "eu-frankfurt-1"
    assert genai_endpoint(region) == f"https://inference.generativeai.{region}.oci.oraclecloud.com"


def test_default_region_is_osaka_not_authentication_region() -> None:
    assert DEFAULT_GENAI_REGION == "ap-osaka-1"


@pytest.mark.parametrize("region", ["", "eu-frankfurt-1", "unknown", "ap-osaka-1/path"])
def test_invalid_region_does_not_fall_back_to_authentication(region: str) -> None:
    with pytest.raises(ValueError):
        genai_endpoint(region)
    with pytest.raises(ValueError):
        genai_config({"region": "ap-tokyo-1"}, region)
