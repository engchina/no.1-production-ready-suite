import { useCallback, useMemo, useRef, useState } from "react";

import type { ChatAttachmentItem, ChatAttachmentKind } from "./chat-attachments";

/** 添付の API の結果（製品の API の応答を詰め替えて返す）。 */
export interface ChatAttachmentUploadResult {
  attachmentId: string;
  fileName: string;
  sizeBytes: number;
  kind?: ChatAttachmentKind;
  /** 読み取りの注意（翻訳済み）。 */
  notes?: string[];
}

export interface ChatAttachmentMessages {
  /** 大きすぎる（上限の表示を受け取る。例: "20 MB"）。 */
  tooLarge: (maxSize: string) => string;
  /** 件数の上限。 */
  tooMany: (maxFiles: number) => string;
  /** 受け付けない種類。 */
  unsupported: string;
  /** API の失敗の理由（製品の API のエラーから作る）。 */
  uploadFailed: (error: unknown) => string;
}

export interface UseChatAttachmentsOptions {
  upload: (file: File) => Promise<ChatAttachmentUploadResult>;
  /** 外す（API の失敗は無視する。期限で消える）。 */
  remove: (attachmentId: string) => Promise<unknown>;
  /** 1 つの大きさの上限（bytes）。無ければ確かめない。 */
  maxBytes?: number;
  /** 件数の上限（読み取り中・添付済みの合計）。 */
  maxFiles?: number;
  /** 受け付ける拡張子（".pdf" など）。無ければ確かめない。 */
  accept?: readonly string[];
  messages: ChatAttachmentMessages;
  formatSize?: (bytes: number) => string;
}

interface Entry extends ChatAttachmentItem {
  attachmentId?: string;
}

let localCounter = 0;
function localId() {
  localCounter += 1;
  return `chat-attachment-${localCounter}`;
}

function extensionOf(name: string) {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

/**
 * 入力欄の添付の状態（3 製品共通。#2517）。選んだファイルを 1 つずつ API で添付し、送信に使う
 * `readyIds`（API の添付の ID）を返す。送信が受け付けられたら `clear()` で入力欄から外す
 * （添付は会話に残る）。上限・種類は API でも確かめるが、送る前に分かるものはここで失敗にする。
 */
export function useChatAttachments(options: UseChatAttachmentsOptions) {
  const [items, setItems] = useState<Entry[]>([]);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  // 読み取り中に外したもの（終わったら API でも消す）。
  const removedWhileUploading = useRef(new Set<string>());

  const update = useCallback((id: string, patch: Partial<Entry>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  // 副作用（API の呼び出し）は state の更新関数の外で行う（StrictMode は更新関数を 2 回呼ぶ）。
  const itemsRef = useRef<Entry[]>([]);
  itemsRef.current = items;

  const startUpload = useCallback(
    (id: string, file: File) => {
      void optionsRef.current
        .upload(file)
        .then((result) => {
          if (removedWhileUploading.current.delete(id)) {
            void optionsRef.current.remove(result.attachmentId).catch(() => undefined);
            return;
          }
          update(id, {
            status: "ready",
            attachmentId: result.attachmentId,
            fileName: result.fileName,
            sizeBytes: result.sizeBytes,
            kind: result.kind,
            notes: result.notes,
          });
        })
        .catch((reason: unknown) => {
          if (removedWhileUploading.current.delete(id)) return;
          update(id, { status: "failed", error: optionsRef.current.messages.uploadFailed(reason) });
        });
    },
    [update]
  );

  const add = useCallback(
    (files: File[]) => {
      const { maxBytes, maxFiles, accept, messages, formatSize } = optionsRef.current;
      let active = itemsRef.current.filter((item) => item.status !== "failed").length;
      const added: Entry[] = [];
      const uploads: [string, File][] = [];
      for (const file of files) {
        const id = localId();
        const base: Entry = { id, fileName: file.name, sizeBytes: file.size };
        let error: string | undefined;
        if (accept && accept.length > 0 && !accept.includes(extensionOf(file.name))) {
          error = messages.unsupported;
        } else if (maxBytes !== undefined && file.size > maxBytes) {
          error = messages.tooLarge((formatSize ?? String)(maxBytes));
        } else if (maxFiles !== undefined && active >= maxFiles) {
          error = messages.tooMany(maxFiles);
        }
        if (error) {
          added.push({ ...base, status: "failed", error });
          continue;
        }
        active += 1;
        added.push({ ...base, status: "uploading" });
        uploads.push([id, file]);
      }
      itemsRef.current = [...itemsRef.current, ...added];
      setItems((current) => [...current, ...added]);
      for (const [id, file] of uploads) startUpload(id, file);
    },
    [startUpload]
  );

  const remove = useCallback((id: string) => {
    const target = itemsRef.current.find((item) => item.id === id);
    if (target?.status === "uploading") removedWhileUploading.current.add(id);
    if (target?.attachmentId) void optionsRef.current.remove(target.attachmentId).catch(() => undefined);
    itemsRef.current = itemsRef.current.filter((item) => item.id !== id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  /** 送信が受け付けられた後に、入力欄から外す（API の添付は会話に残る）。 */
  const clear = useCallback(() => {
    setItems((current) => current.filter((item) => item.status === "uploading"));
  }, []);

  /** 送る前の添付を API の一覧から戻す（画面を開き直したとき）。 */
  const restore = useCallback((results: ChatAttachmentUploadResult[]) => {
    setItems(
      results.map((result) => ({
        id: localId(),
        attachmentId: result.attachmentId,
        fileName: result.fileName,
        sizeBytes: result.sizeBytes,
        kind: result.kind,
        notes: result.notes,
        status: "ready" as const,
      }))
    );
  }, []);

  const readyIds = useMemo(
    () => items.flatMap((item) => (item.status === "ready" && item.attachmentId ? [item.attachmentId] : [])),
    [items]
  );
  /** 送信に使う添付（発話の表示に使う）。 */
  const readyItems = useMemo(
    () => items.filter((item) => item.status === "ready" && item.attachmentId),
    [items]
  );
  const uploading = items.some((item) => item.status === "uploading");

  return { items: items as ChatAttachmentItem[], add, remove, clear, restore, readyIds, readyItems, uploading };
}

/** 3 製品の添付の API（`/api/chat-attachments`）が返す添付（backend の `ChatAttachmentData`）。 */
export interface ChatAttachmentApiItem {
  attachment_id: string;
  file_name: string;
  kind: ChatAttachmentKind;
  size_bytes: number;
  warnings?: string[];
}

/** 添付の上限（backend の `ChatAttachmentLimitsData`）。 */
export interface ChatAttachmentApiLimits {
  max_bytes: number;
  max_files: number;
  ttl_hours: number;
  accept: string[];
  image_enabled: boolean;
  /** 製品の「運用設定 › チャットの添付」で有効か。無効なら入力欄に添付の操作を出さない。 */
  enabled: boolean;
}

/** 発話に残る添付（backend の `ChatAttachmentReference`）。 */
export interface ChatAttachmentApiReference {
  attachment_id: string;
  file_name: string;
  kind: ChatAttachmentKind;
  size_bytes?: number;
}

/** API の添付を `useChatAttachments` の結果にする（警告の code を製品の文言にする）。 */
export function chatAttachmentUploadResult(
  item: ChatAttachmentApiItem,
  warningLabel: (code: string) => string | undefined
): ChatAttachmentUploadResult {
  return {
    attachmentId: item.attachment_id,
    fileName: item.file_name,
    sizeBytes: item.size_bytes,
    kind: item.kind,
    notes: (item.warnings ?? []).flatMap((code) => {
      const label = warningLabel(code);
      return label ? [label] : [];
    }),
  };
}

/** 発話に残る添付を表示の項目にする（`ChatTurn` の `questionAttachments`）。 */
export function chatAttachmentItems(references: readonly ChatAttachmentApiReference[] | undefined): ChatAttachmentItem[] {
  return (references ?? []).map((reference) => ({
    id: reference.attachment_id,
    fileName: reference.file_name,
    sizeBytes: reference.size_bytes ?? 0,
    kind: reference.kind,
  }));
}
