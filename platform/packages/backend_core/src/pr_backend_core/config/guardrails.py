"""3 製品共通の安全チェックの有効化（`PLATFORM_GUARDRAILS_*`。#5061）。

検査と判定は `pr_system_settings.guardrails`。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GuardrailSettings(BaseModel):
    guardrails_input_enabled: bool = Field(
        default=False, description="質問を OCI Guardrails で検査する（既定は無効）"
    )
    guardrails_output_enabled: bool = Field(
        default=False, description="回答を OCI Guardrails で検査する（既定は無効）"
    )
