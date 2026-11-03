"""廃止した問い合わせ情報を含む保存契約は、署名が正しくても受け入れない。"""

import hashlib
import json

import pytest

from rag_engine.chunking.storage import _chunk_contracts, _validate_chunk_contracts


def test_current_contract_is_accepted() -> None:
    _validate_chunk_contracts(_chunk_contracts())


@pytest.mark.parametrize("mutation", ["retired", "missing", "signature", "version"])
def test_incompatible_contract_requires_rechunking(mutation: str) -> None:
    contract = _chunk_contracts()
    if mutation == "retired":
        contract.update(
            {
                "inquiry_chunk_metadata_schema_version": 1,
                "inquiry_profile_contract_hash": "synthetic",
            }
        )
        content = {key: value for key, value in contract.items() if key != "fingerprint"}
        contract["fingerprint"] = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    elif mutation == "missing":
        del contract["search_text_schema_version"]
    elif mutation == "signature":
        contract["fingerprint"] = "incorrect"
    else:
        contract["chunk_metadata_schema_version"] = "unsupported"
    with pytest.raises(ValueError, match="再チャンキング"):
        _validate_chunk_contracts(contract)
