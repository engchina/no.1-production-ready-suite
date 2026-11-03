"""長い識別子状本文でも秘密値の除去を繰り返し走査で待たせない。"""

import pytest

from pr_backend_core.chat_progress import redact_chat_progress_text


@pytest.mark.timeout(5)
def test_long_hyphenated_nonsecret_text_is_preserved() -> None:
    text = "synthetic-input-" * 10000
    assert redact_chat_progress_text(text) == text


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1-password=private-value", "1-password=***"),
        ('prefix-api-key-2="private-value"', "prefix-api-key-2=***"),
        ("password=private-value", "password=***"),
        ("token: private-value", "token: ***"),
        ('{"api_key2": "private-value"}', '{"api_key2": "***"}'),
        ("Bearer private-value", "Bearer ***"),
        ("https://user:private-value@synthetic.invalid/path", "https://***@synthetic.invalid/path"),
        (
            "prefix-https://user:private-value@synthetic.invalid",
            "prefix-https://***@synthetic.invalid",
        ),
        (
            "note: (https://user:private-value@synthetic.invalid)",
            "note: (https://***@synthetic.invalid)",
        ),
    ],
)
def test_secret_detection_is_retained_at_identifier_and_url_boundaries(
    value: str, expected: str
) -> None:
    assert redact_chat_progress_text(value) == expected
