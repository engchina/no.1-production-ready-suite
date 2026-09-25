import { useCallback, useMemo, useState } from "react";

import {
  useSettingsDraftGuard,
  useUnsavedChangesGuard,
  type DraftGuardMessages,
} from "@engchina/production-ready-system-settings";
import { useConfirm } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

/**
 * 未保存変更の離脱ガード（platform UX 契約 workspace-state.md「未保存変更の離脱ガード」。#87）。
 *
 * 共有パッケージの `useUnsavedChangesGuard`（内部リンク・サイドナビの click と `beforeunload`）を
 * Agent の i18n 文言で包む。dirty でないときは何も妨げない。
 */

function leaveMessages(): DraftGuardMessages {
  return {
    discardTitle: t("guard.discardTitle"),
    discardDescription: t("guard.discardDescription"),
    discardConfirm: t("guard.discardConfirm"),
  };
}

/** 設定画面（1 画面 = 1 フォーム）の離脱ガード。保存中の離脱も止める。 */
export function useSettingsLeaveGuard(isDirty: boolean, busy = false): () => Promise<boolean> {
  return useSettingsDraftGuard(isDirty, busy, leaveMessages());
}

/**
 * 一覧の中のエディタ（新規 / 編集フォーム）の離脱ガード。
 * 戻り値の `confirmClose()` は、画面内の「キャンセル」や別の対象の編集への切替で、
 * 未保存の編集を捨てる前に呼ぶ（dirty でなければ確認せず true）。
 */
export function useEditorLeaveGuard(isDirty: boolean, busy = false): { confirmClose: () => Promise<boolean> } {
  const confirm = useConfirm();
  const confirmLeave = useCallback(async () => {
    if (busy) return false;
    if (!isDirty) return true;
    const messages = leaveMessages();
    return confirm({
      title: messages.discardTitle,
      description: messages.discardDescription,
      confirmLabel: messages.discardConfirm,
      tone: "danger",
      dismissOnOverlay: false,
    });
  }, [busy, confirm, isDirty]);
  useUnsavedChangesGuard(isDirty || busy, confirmLeave);

  const confirmClose = useCallback(async () => {
    if (!isDirty) return true;
    return confirm({
      title: t("guard.discardTitle"),
      description: t("guard.closeDescription"),
      confirmLabel: t("guard.closeConfirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
  }, [confirm, isDirty]);
  return { confirmClose };
}

/**
 * 1 画面に複数のエディタがあるとき（業務 Agent の一覧など）、各エディタの dirty を集約する。
 * 各エディタは `report(key, dirty)` で自分の状態を知らせ、unmount 時に false を報告する。
 */
export function useDirtySources(): { anyDirty: boolean; report: (key: string, dirty: boolean) => void } {
  const [dirtyKeys, setDirtyKeys] = useState<ReadonlySet<string>>(() => new Set());
  const report = useCallback((key: string, dirty: boolean) => {
    setDirtyKeys((current) => {
      if (current.has(key) === dirty) return current;
      const next = new Set(current);
      if (dirty) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  return useMemo(() => ({ anyDirty: dirtyKeys.size > 0, report }), [dirtyKeys, report]);
}

/** 下書きと保存済みの基準を比べる。順序に意味のない配列は呼び出し側で並べ替えてから渡す。 */
export function sameDraft(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
