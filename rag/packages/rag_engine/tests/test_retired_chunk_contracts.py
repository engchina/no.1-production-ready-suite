"""廃止した推定メタデータを含む履歴だけを互換で読み、破損した契約は拒否する。"""

import hashlib
import json

import pytest

from rag_engine.chunking.storage import _chunk_contracts, _validate_chunk_contracts


def signed(value: dict) -> dict:
    contents = {key: item for key, item in value.items() if key != "fingerprint"}
    return {
        **contents,
        "fingerprint": hashlib.sha256(json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def legacy() -> dict:
    return signed({**_chunk_contracts(), "inquiry_chunk_metadata_schema_version": 1, "inquiry_profile_contract_hash": "synthetic-retired"})


def test_retired_only_legacy_contract_is_readable() -> None:
    _validate_chunk_contracts(_chunk_contracts())
    _validate_chunk_contracts(legacy())


@pytest.mark.parametrize("change", ["signature", "known_version", "extra_field"])
def test_compatibility_does_not_accept_corruption_or_unknown_changes(change: str) -> None:
    value = legacy()
    if change == "signature":
        value["fingerprint"] = "invalid"
    elif change == "known_version":
        key = next(key for key in _chunk_contracts() if key != "fingerprint")
        value[key] = "unsupported-version"
        value = signed(value)
    else:
        value = signed({**value, "unexpected": True})
    with pytest.raises(ValueError):
        _validate_chunk_contracts(value)
