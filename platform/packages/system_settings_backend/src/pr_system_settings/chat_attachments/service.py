"""会話の添付のサービス（受け付け・見え方・会話への結び付け・期限。#2517）。

- 持ち主: 会話を始める前の添付は添付した人、会話に結び付けたら会話の持ち主（system_admin が
  ほかの利用者の会話で添付したら持ち主はその利用者。`UPLOADED_BY_USER_UUID` は system_admin）。
- 見え方: 「AI 活用」の規則（`auth.visibility`）。他人の添付は 404。製品は自分の製品の行だけを扱う。
- 期限: 添付・送信に使った時刻 + `chat_attachment_ttl_hours`。期限の切れた行は、添付の操作のたびに
  `PURGE_BATCH_SIZE` 件ずつ消す（スケジューラーを持たない）。
- 会話の権限（会話が見えるか・持ち主）は製品が判定し、`conversation_owner` で渡す。
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from anyio import to_thread

from ..auth.visibility import can_access_user_data
from .errors import ChatAttachmentError, attachment_not_found
from .limits import PURGE_BATCH_SIZE, ChatAttachmentLimits, chat_attachment_limits, file_too_large
from .models import (
    ChatAttachmentData,
    ChatAttachmentLimitsData,
    ChatAttachmentListData,
    ChatAttachmentProduct,
    ChatAttachmentRecord,
    ChatAttachmentReference,
    ParsedAttachment,
)
from .parsing import ImageReader, accepted_extensions, parse_attachment
from .store import ChatAttachmentStore
from .tools import ChatAttachmentToolset

logger = logging.getLogger(__name__)

MAX_FILE_NAME_CHARACTERS = 400


class AttachmentPrincipal(Protocol):
    @property
    def user_uuid(self) -> str: ...

    @property
    def is_system_admin(self) -> bool: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_file_name(file_name: str) -> str:
    """path を除いた名前（制御文字を除き、長さを切る）。"""
    base = posixpath.basename(file_name.replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable())
    if not base:
        raise ChatAttachmentError(
            422, "ファイル名がありません。", code="CHAT_ATTACHMENT_NAME_INVALID"
        )
    if len(base) > MAX_FILE_NAME_CHARACTERS:
        stem, extension = posixpath.splitext(base)
        base = stem[: MAX_FILE_NAME_CHARACTERS - len(extension)] + extension
    return base


def _too_many(max_files: int) -> ChatAttachmentError:
    return ChatAttachmentError(
        409,
        f"1 つの会話に添付できるファイルは {max_files} 件までです。不要な添付を外してください。",
        code="CHAT_ATTACHMENT_TOO_MANY",
    )


class ChatAttachmentService:
    def __init__(
        self,
        store: ChatAttachmentStore,
        *,
        product: ChatAttachmentProduct,
        settings: Callable[[], object],
        image_reader: Callable[[], ImageReader | None] = lambda: None,
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.store = store
        # 製品の「運用設定 › チャットの添付」で有効か（`settings.ChatAttachmentSettingsProvider`）。
        self.enabled = enabled
        self.product: ChatAttachmentProduct = product
        self._settings = settings
        self._image_reader = image_reader
        self._clock = clock

    # ---- 設定 ----

    def limits(self) -> ChatAttachmentLimits:
        return chat_attachment_limits(self._settings())

    def limits_data(self) -> ChatAttachmentLimitsData:
        limits = self.limits()
        image_enabled = self._image_reader() is not None
        return ChatAttachmentLimitsData(
            max_bytes=limits.max_bytes,
            max_files=limits.max_files,
            ttl_hours=limits.ttl_hours,
            accept=accepted_extensions(image_enabled=image_enabled),
            image_enabled=image_enabled,
            enabled=self.enabled(),
        )

    def _require_enabled(self) -> None:
        if not self.enabled():
            raise ChatAttachmentError(
                403,
                "この製品ではチャットの添付を使えません。管理者が「運用設定 › チャットの添付」で"
                "有効にすると使えます。",
                code="CHAT_ATTACHMENT_DISABLED",
            )

    def _expires_at(self, now: datetime) -> datetime:
        return now + timedelta(hours=self.limits().ttl_hours)

    def purge_expired(self) -> int:
        """期限の切れた行を上限の件数まで消す（失敗しても操作は止めない）。"""
        try:
            return self.store.purge_expired(now=self._clock(), limit=PURGE_BATCH_SIZE)
        except ChatAttachmentError:
            raise
        except Exception:  # 消せなくても次の操作で消す
            logger.warning("chat_attachment_purge_failed", exc_info=True)
            return 0

    # ---- 見え方 ----

    def _visible(self, principal: AttachmentPrincipal, attachment_id: str) -> ChatAttachmentRecord:
        record = self.store.get(attachment_id)
        if (
            record is None
            or record.product != self.product
            or record.expires_at <= self._clock()
            or not can_access_user_data(principal, record.owner_user_uuid)
        ):
            raise attachment_not_found()
        return record

    # ---- 受け付け ----

    async def upload(
        self,
        principal: AttachmentPrincipal,
        *,
        file_name: str,
        data: bytes,
        conversation_id: str | None = None,
        conversation_owner_user_uuid: str | None = None,
    ) -> ChatAttachmentData:
        """ファイルを解析して保存する。`conversation_id` があれば、その会話に結び付ける。

        `conversation_owner_user_uuid` は会話の持ち主（製品が会話の権限を確かめてから渡す）。
        """
        self._require_enabled()
        limits = self.limits()
        if len(data) > limits.max_bytes:
            raise file_too_large(limits.max_bytes)
        name = _safe_file_name(file_name)
        await to_thread.run_sync(self.purge_expired)
        now = self._clock()
        existing = await to_thread.run_sync(
            lambda: self._existing(principal, conversation_id, now=now)
        )
        if len(existing) >= limits.max_files:
            raise _too_many(limits.max_files)
        spec, parsed = await parse_attachment(name, data, image_reader=self._image_reader())
        owner = (
            conversation_owner_user_uuid
            if conversation_id and conversation_owner_user_uuid
            else principal.user_uuid
        )
        record = ChatAttachmentRecord(
            attachment_id=str(uuid.uuid4()),
            product=self.product,
            owner_user_uuid=owner,
            uploaded_by_user_uuid=principal.user_uuid,
            conversation_id=conversation_id or None,
            file_name=name,
            media_type=spec.media_type,
            kind=spec.kind,
            size_bytes=len(data),
            content_sha256=hashlib.sha256(data).hexdigest(),
            outline=parsed.outline(),
            created_at=now,
            expires_at=self._expires_at(now),
        )
        await to_thread.run_sync(lambda: self.store.insert(record, data, parsed))
        return ChatAttachmentData.from_record(record, viewer_user_uuid=principal.user_uuid)

    def _existing(
        self, principal: AttachmentPrincipal, conversation_id: str | None, *, now: datetime
    ) -> list[ChatAttachmentRecord]:
        if conversation_id:
            return self.store.list_for_conversation(self.product, conversation_id, now=now)
        return self.store.list_pending(self.product, principal.user_uuid, now=now)

    # ---- 一覧・削除 ----

    def list_items(
        self, principal: AttachmentPrincipal, *, conversation_id: str | None = None
    ) -> ChatAttachmentListData:
        """会話の添付（`conversation_id` が無ければ、自分が添付して送る前のもの）。

        会話の権限は製品が先に確かめる。
        """
        self.purge_expired()
        records = self._existing(principal, conversation_id, now=self._clock())
        return ChatAttachmentListData(
            items=[
                ChatAttachmentData.from_record(record, viewer_user_uuid=principal.user_uuid)
                for record in records
            ],
            limits=self.limits_data(),
        )

    def delete(self, principal: AttachmentPrincipal, attachment_id: str) -> None:
        self._visible(principal, attachment_id)
        self.store.delete(attachment_id)

    def delete_conversation(self, conversation_id: str) -> int:
        """会話の削除に合わせて、その会話の添付を消す（会話の権限は製品が確かめる）。"""
        return self.store.delete_conversation(self.product, conversation_id)

    # ---- 送信 ----

    def prepare_attachments(
        self,
        principal: AttachmentPrincipal,
        attachment_ids: Sequence[str],
        *,
        conversation_id: str | None,
    ) -> list[ChatAttachmentReference]:
        """送信の添付を確かめ、発話に残す参照を返す（まだ会話に結び付けない）。

        会話を送信で作る製品（会話の ID が送信の後に決まる）は、これで確かめてから送信を受け付け、
        会話の ID が決まった後に `bind_references` で結び付ける。見えない・期限の切れた添付は 404、
        別の会話の添付は 409、会話の件数の上限を超えると 409。
        """
        self._require_enabled()
        self.purge_expired()
        unique_ids = list(dict.fromkeys(attachment_ids))
        records = [self._visible(principal, attachment_id) for attachment_id in unique_ids]
        for record in records:
            if record.conversation_id and record.conversation_id != conversation_id:
                raise ChatAttachmentError(
                    409,
                    f"「{record.file_name}」は別の会話の添付です。この会話に添付し直してください。",
                    code="CHAT_ATTACHMENT_OTHER_CONVERSATION",
                )
        limits = self.limits()
        current: set[str] = set()
        if conversation_id:
            current = {
                record.attachment_id
                for record in self.store.list_for_conversation(
                    self.product, conversation_id, now=self._clock()
                )
            }
        if len(current | set(unique_ids)) > limits.max_files:
            raise _too_many(limits.max_files)
        return [
            ChatAttachmentReference(
                attachment_id=record.attachment_id,
                file_name=record.file_name,
                kind=record.kind,
                size_bytes=record.size_bytes,
            )
            for record in records
        ]

    def bind_references(
        self,
        references: Sequence[ChatAttachmentReference],
        *,
        conversation_id: str,
        owner_user_uuid: str,
    ) -> None:
        """確かめた添付を会話に結び付け、会話の添付の期限を延ばす（`prepare_attachments` の後）。"""
        expires_at = self._expires_at(self._clock())
        if references:
            self.store.bind(
                [reference.attachment_id for reference in references],
                conversation_id=conversation_id,
                owner_user_uuid=owner_user_uuid,
                expires_at=expires_at,
            )
        self.store.touch_conversation(self.product, conversation_id, expires_at=expires_at)

    def attach_to_conversation(
        self,
        principal: AttachmentPrincipal,
        attachment_ids: Sequence[str],
        *,
        conversation_id: str,
        owner_user_uuid: str,
    ) -> list[ChatAttachmentReference]:
        """送信の添付を確かめて会話に結び付け、発話に残す参照を返す。

        会話の ID が送信の前に決まる製品が使う。
        """
        references = self.prepare_attachments(
            principal, attachment_ids, conversation_id=conversation_id
        )
        self.bind_references(
            references, conversation_id=conversation_id, owner_user_uuid=owner_user_uuid
        )
        return references

    # ---- 読む ----

    def conversation_records(self, conversation_id: str) -> list[ChatAttachmentRecord]:
        """会話の期限内の添付（会話の権限は製品が確かめる）。"""
        return self.store.list_for_conversation(self.product, conversation_id, now=self._clock())

    def parsed(self, attachment_id: str) -> ParsedAttachment | None:
        record = self.store.get(attachment_id)
        if record is None or record.product != self.product or record.expires_at <= self._clock():
            return None
        return self.store.parsed(attachment_id)

    def toolset(self, conversation_id: str) -> ChatAttachmentToolset:
        """会話の添付を読むツール（添付が無い・無効にした製品は空。`bool(toolset)` が False）。"""
        if not self.enabled():
            return ChatAttachmentToolset([], self.parsed)
        return ChatAttachmentToolset(self.conversation_records(conversation_id), self.parsed)
