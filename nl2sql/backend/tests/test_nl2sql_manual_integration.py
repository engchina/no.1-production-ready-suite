"""Manual integration script の lightweight 回帰テスト。"""

from __future__ import annotations

import argparse

import pytest

from app.features.nl2sql.models import Nl2SqlEngine
from scripts import nl2sql_manual_integration as script


def test_parse_engines_rejects_auto() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        script._parse_engines("auto")


def test_parse_engines_accepts_concrete_select_ai_engines() -> None:
    assert script._parse_engines("select_ai,select_ai_agent") == [
        Nl2SqlEngine.SELECT_AI,
        Nl2SqlEngine.SELECT_AI_AGENT,
    ]


def test_manual_integration_preview_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = script.main(
        [
            "--engines",
            "enterprise_ai_direct",
            "--question",
            "請求金額を確認したい",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[ok] diagnostics:" in output
    assert "[ok] preview_enterprise_ai_direct:" in output
    assert "ORACLE_PASSWORD" not in output


def test_manual_integration_cleanup_assets_is_dry_run_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        script,
        "_diagnostics_with_timeout",
        lambda _require_oracle, _timeout: pytest.fail("cleanup dry-run should not run diagnostics"),
    )

    exit_code = script.main(
        [
            "--cleanup-assets",
            "--engines",
            "select_ai_agent",
            "--profile-id",
            "default",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[ok] cleanup_select_ai_agent:" in output
    assert "status=dry_run" in output
    assert "executed=False" in output
    assert "preview_select_ai_agent" not in output


def test_manual_integration_confirm_cleanup_requires_cleanup_flag() -> None:
    with pytest.raises(SystemExit):
        script.main(["--confirm-cleanup"])


def test_manual_integration_stops_when_required_oracle_diagnostics_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        script,
        "_diagnostics_with_timeout",
        lambda _require_oracle, _timeout: script.StepResult(
            name="diagnostics", ok=False, message="diagnostics timeout after 1.0s"
        ),
    )
    monkeypatch.setattr(
        script,
        "_refresh_catalog",
        lambda _enabled: pytest.fail("schema refresh should not run after diagnostics failure"),
    )

    exit_code = script.main(["--require-oracle", "--diagnostics-timeout", "1"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "[ng] diagnostics: diagnostics timeout after 1.0s" in output
