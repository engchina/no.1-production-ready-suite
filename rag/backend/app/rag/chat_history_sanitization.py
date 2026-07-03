"""既存チャット履歴を安全チェックで再検査・上書きする運用 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from app.api.routes.chat import BLOCKED_MESSAGE_PLACEHOLDER
from app.clients.oracle import OracleClient, StoredMessage
from app.config import Settings, get_settings
from app.rag.business_view_config import resolve_business_view_settings
from app.rag.guardrails import GuardrailPolicy, GuardrailResult

BLOCKED_ANSWER_PLACEHOLDER = "安全ポリシーにより回答を保存しませんでした。"


@dataclass
class SanitizationCounts:
    """原文を含まない実行集計。"""

    conversations: int = 0
    scanned: int = 0
    updated: int = 0
    masked: int = 0
    blocked: int = 0
    failed: int = 0


async def sanitize_chat_history(
    *,
    apply: bool,
    batch_size: int = 100,
    oracle: OracleClient | None = None,
    settings: Settings | None = None,
) -> SanitizationCounts:
    """会話単位で Business View 設定を解決し、既存メッセージを冪等に浄化する。"""
    client = oracle or OracleClient()
    global_settings = settings or get_settings()
    counts = SanitizationCounts()
    offset = 0
    while True:
        conversations = await client.list_conversations_for_guardrail_migration(
            limit=batch_size, offset=offset
        )
        if not conversations:
            break
        for conversation in conversations:
            counts.conversations += 1
            messages = await client.list_messages_for_guardrail_migration(conversation.id)
            config = await client.get_business_view_config_for_guardrail_migration(
                conversation.business_view_id
            )
            if config is None:
                counts.failed += len(messages)
                continue
            effective_settings, _ = resolve_business_view_settings(global_settings, config)
            # 移行 CLI は policy preset の静的 core を使い、stage service の可用性に依存しない。
            effective_settings = effective_settings.model_copy(
                update={"rag_guardrail_service_enabled": False}
            )
            policy = GuardrailPolicy(effective_settings)
            await _sanitize_conversation(
                client=client,
                messages=messages,
                policy=policy,
                apply=apply,
                counts=counts,
            )
        offset += len(conversations)
    return counts


async def _sanitize_conversation(
    *,
    client: OracleClient,
    messages: list[StoredMessage],
    policy: GuardrailPolicy,
    apply: bool,
    counts: SanitizationCounts,
) -> None:
    blocked_user_ids: set[str] = set()
    for message in messages:
        counts.scanned += 1
        try:
            if message.role == "USER":
                result, status = await _sanitize_user_message(message, policy)
                if not result.allowed:
                    blocked_user_ids.add(message.id)
                    counts.blocked += 1
                    content = BLOCKED_MESSAGE_PLACEHOLDER
                    status = "ERROR"
                else:
                    content = result.sanitized_text
            elif message.role == "ASSISTANT":
                if message.reply_to_message_id in blocked_user_ids:
                    result = GuardrailResult(
                        allowed=False,
                        sanitized_text=BLOCKED_ANSWER_PLACEHOLDER,
                        findings=[],
                    )
                    content = BLOCKED_ANSWER_PLACEHOLDER
                    status = "ERROR"
                    counts.blocked += 1
                else:
                    result = await asyncio.to_thread(policy.validate_answer, message.content)
                    content = (
                        result.sanitized_text if result.allowed else BLOCKED_ANSWER_PLACEHOLDER
                    )
                    status = "COMPLETE" if result.allowed else "ERROR"
                    if not result.allowed:
                        counts.blocked += 1
            else:
                continue

            warnings = list(dict.fromkeys([*message.guardrail_warnings, *result.warnings]))
            if any(
                finding.code in {"sensitive_identifier_redacted", "oci_pii_detected"}
                for finding in result.findings
            ):
                counts.masked += 1
            changed = (
                content != message.content
                or status != message.status
                or warnings != message.guardrail_warnings
            )
            if changed:
                counts.updated += 1
                if apply:
                    await client.update_message_for_guardrail_migration(
                        message_id=message.id,
                        content=content,
                        status=status,
                        guardrail_warnings=warnings,
                    )
        except Exception:  # noqa: BLE001 - 集計だけを返し原文/例外本文を出さない
            counts.failed += 1


async def _sanitize_user_message(
    message: StoredMessage, policy: GuardrailPolicy
) -> tuple[GuardrailResult, str]:
    if message.status != "COMPLETE":
        return (
            GuardrailResult(
                allowed=False,
                sanitized_text=BLOCKED_MESSAGE_PLACEHOLDER,
                findings=[],
            ),
            "ERROR",
        )
    return await asyncio.to_thread(policy.validate_query, message.content), "COMPLETE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="既存チャット履歴を安全に浄化します。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="変更件数だけを確認します(既定)。")
    mode.add_argument("--apply", action="store_true", help="浄化済み内容を Oracle へ上書きします。")
    parser.add_argument("--batch-size", type=int, default=100, choices=range(1, 1001))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    counts = asyncio.run(sanitize_chat_history(apply=bool(args.apply), batch_size=args.batch_size))
    payload = {"mode": "apply" if args.apply else "dry-run", **asdict(counts)}
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print("\n".join(f"{key}: {value}" for key, value in payload.items()))
    return 0 if counts.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
