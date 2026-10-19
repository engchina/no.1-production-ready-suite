import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  chatAttachmentUploadResult,
  formatAttachmentSize,
  useChatAttachments,
  type ChatAttachmentItem,
  type ChatComposerAttachments,
} from "@production-ready/ui";
import { CHAT_ATTACHMENT_SETTINGS_QUERY_KEY } from "@production-ready/system-settings";

import { ApiError, agentApi } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

/** 添付の警告の code を文言にする（backend の `pr_system_settings.chat_attachments.parsing`）。 */
function warningLabel(code: string): string | undefined {
  const key = `chat.attachments.warning.${code}` as I18nKey;
  const label = t(key);
  return label === key ? undefined : label;
}

/**
 * チャットの入力欄の添付（#2517）。送る前の添付の一覧と上限を API から読み（製品の設定で無効なら出さない）、
 * `ChatComposer` の `attachments` と、送信に使う添付（`take`）を返す。添付は送信で会話に結び付ける。
 */
export function useAgentChatAttachments() {
  const pendingList = useQuery({
    // 設定の画面で保存したら、この key を捨てて読み直す（同じ prefix）。
    queryKey: [...CHAT_ATTACHMENT_SETTINGS_QUERY_KEY, "composer"],
    queryFn: () => agentApi.listChatAttachments(),
    staleTime: 30_000,
  });
  const limits = pendingList.data?.limits;
  const attachments = useChatAttachments({
    upload: async (file) => chatAttachmentUploadResult(await agentApi.uploadChatAttachment(file), warningLabel),
    remove: (attachmentId) => agentApi.deleteChatAttachment(attachmentId),
    maxBytes: limits?.max_bytes,
    maxFiles: limits?.max_files,
    accept: limits?.accept,
    formatSize: formatAttachmentSize,
    messages: {
      tooLarge: (size) => t("chat.attachments.tooLarge", { size }),
      tooMany: (count) => t("chat.attachments.tooMany", { count }),
      unsupported: t("chat.attachments.unsupported"),
      uploadFailed: (error) => (error instanceof ApiError ? error.message : t("chat.attachments.failed")),
    },
  });
  // 開き直したときは、送る前の添付を戻す（1 回だけ）。
  const restored = useRef(false);
  const { restore } = attachments;
  useEffect(() => {
    if (restored.current || !pendingList.data) return;
    restored.current = true;
    if (pendingList.data.items.length) {
      restore(pendingList.data.items.map((item) => chatAttachmentUploadResult(item, warningLabel)));
    }
  }, [pendingList.data, restore]);

  const enabled = Boolean(limits?.enabled ?? false);
  const composer = useMemo<ChatComposerAttachments | undefined>(
    () =>
      enabled && limits
        ? {
            items: attachments.items,
            onAddFiles: attachments.add,
            onRemove: attachments.remove,
            accept: limits.accept.join(","),
            hint: t("chat.attachments.hint", {
              size: formatAttachmentSize(limits.max_bytes),
              count: limits.max_files,
              hours: limits.ttl_hours,
            }),
            labels: {
              add: t("chat.attachments.add"),
              drop: t("chat.attachments.drop"),
              list: t("chat.attachments.list"),
              remove: (name) => t("chat.attachments.remove", { name }),
              uploading: t("chat.attachments.uploading"),
            },
          }
        : undefined,
    [enabled, limits, attachments.items, attachments.add, attachments.remove]
  );

  /** 送信に使う添付を取り出し、入力欄から外す（会話の欄の送った質問の下に出す）。 */
  function take(): { ids: string[]; items: ChatAttachmentItem[] } {
    if (!enabled) return { ids: [], items: [] };
    const items = attachments.readyItems.map((item) => ({ ...item }));
    const ids = attachments.readyIds;
    if (ids.length) attachments.clear();
    return { ids, items };
  }

  return { composer, take, uploading: attachments.uploading };
}
