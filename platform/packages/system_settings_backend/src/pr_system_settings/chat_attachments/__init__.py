"""会話の添付（チャットの作業ファイル。3 製品共通。#2517）。

会話の期間だけ使うファイルを Oracle AI Database（`PLATFORM_CHAT_ATTACHMENTS`）に保存し、
文字と節・表へ解析して、モデルには索引だけを渡し、必要な部分をツールで読ませる。

- `parsing`: 種類の判定と解析（PDF・Word・PowerPoint・Excel・CSV・テキスト・画像）
- `store`: 保存（Oracle / メモリ）
- `service`: 受け付け・見え方・会話への結び付け・期限
- `tools`: 読むツールの中核（`chat_attachment_list` / `_read_section` / `_read_rows` / `_find`）
- `router`: API（`/chat-attachments`）
"""

from .errors import ChatAttachmentError
from .limits import (
    CHAT_ATTACHMENT_DEFAULT_MAX_BYTES,
    CHAT_ATTACHMENT_DEFAULT_MAX_FILES,
    CHAT_ATTACHMENT_DEFAULT_TTL_HOURS,
    CHAT_ATTACHMENT_MAX_BYTES_MAX,
    CHAT_ATTACHMENT_MAX_BYTES_MIN,
    CHAT_ATTACHMENT_MAX_FILES_MAX,
    CHAT_ATTACHMENT_MAX_FILES_MIN,
    CHAT_ATTACHMENT_TTL_HOURS_MAX,
    CHAT_ATTACHMENT_TTL_HOURS_MIN,
    ChatAttachmentLimits,
    chat_attachment_limits,
)
from .models import (
    ChatAttachmentData,
    ChatAttachmentListData,
    ChatAttachmentRecord,
    ChatAttachmentReference,
    ParsedAttachment,
)
from .parsing import IMAGE_READING_PROMPT, ImageReader, parse_attachment
from .router import build_chat_attachment_router
from .service import ChatAttachmentService
from .settings import (
    CHAT_ATTACHMENT_SETTINGS_TABLE,
    ChatAttachmentSettingsProvider,
    ChatAttachmentSettingsRecord,
    InMemoryChatAttachmentSettingsStore,
    build_chat_attachment_settings_router,
)
from .store import (
    CHAT_ATTACHMENT_TABLES,
    CHAT_ATTACHMENTS_TABLE,
    ChatAttachmentStore,
    InMemoryChatAttachmentStore,
    OracleChatAttachmentStore,
)
from .tools import (
    CHAT_ATTACHMENT_TOOL_NAMES,
    CHAT_ATTACHMENT_TOOL_SPECS,
    ChatAttachmentToolError,
    ChatAttachmentToolset,
    ChatAttachmentToolSpec,
)

__all__ = [
    "CHAT_ATTACHMENT_SETTINGS_TABLE",
    "ChatAttachmentSettingsProvider",
    "ChatAttachmentSettingsRecord",
    "InMemoryChatAttachmentSettingsStore",
    "build_chat_attachment_settings_router",
    "CHAT_ATTACHMENTS_TABLE",
    "CHAT_ATTACHMENT_TABLES",
    "CHAT_ATTACHMENT_DEFAULT_MAX_BYTES",
    "CHAT_ATTACHMENT_DEFAULT_MAX_FILES",
    "CHAT_ATTACHMENT_DEFAULT_TTL_HOURS",
    "CHAT_ATTACHMENT_MAX_BYTES_MAX",
    "CHAT_ATTACHMENT_MAX_BYTES_MIN",
    "CHAT_ATTACHMENT_MAX_FILES_MAX",
    "CHAT_ATTACHMENT_MAX_FILES_MIN",
    "CHAT_ATTACHMENT_TOOL_NAMES",
    "CHAT_ATTACHMENT_TOOL_SPECS",
    "CHAT_ATTACHMENT_TTL_HOURS_MAX",
    "CHAT_ATTACHMENT_TTL_HOURS_MIN",
    "ChatAttachmentData",
    "ChatAttachmentError",
    "ChatAttachmentLimits",
    "ChatAttachmentListData",
    "ChatAttachmentRecord",
    "ChatAttachmentReference",
    "ChatAttachmentService",
    "ChatAttachmentStore",
    "ChatAttachmentToolError",
    "ChatAttachmentToolSpec",
    "ChatAttachmentToolset",
    "IMAGE_READING_PROMPT",
    "ImageReader",
    "InMemoryChatAttachmentStore",
    "OracleChatAttachmentStore",
    "ParsedAttachment",
    "build_chat_attachment_router",
    "chat_attachment_limits",
    "parse_attachment",
]
