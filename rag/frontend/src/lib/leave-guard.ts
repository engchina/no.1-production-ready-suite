import { useEffect, useRef } from "react";
import {
  useSettingsDraftGuard,
  useUnsavedChangesGuard,
  type DraftGuardMessages,
} from "@engchina/production-ready-system-settings";

import { t } from "@/lib/i18n";

/**
 * 未保存変更の離脱ガード（platform `docs/ux-contracts/workspace-state.md`）。
 *
 * 内部リンク（サイドナビ含む）の click と `beforeunload` は共有 hook が守る。
 * コマンドパレットやログアウトのように `navigate()` で移動する経路は
 * `confirmPendingLeave()` を先に呼び、同じ確認ダイアログを通す。
 */
export function draftGuardMessages(): DraftGuardMessages {
  return {
    discardTitle: t("common.leaveGuard.title"),
    discardDescription: t("common.leaveGuard.description"),
    discardConfirm: t("common.leaveGuard.confirm"),
  };
}

const activeGuards = new Set<{ current: () => Promise<boolean> }>();

function useRegisterLeaveGuard(enabled: boolean, confirmLeave: () => Promise<boolean>) {
  const ref = useRef(confirmLeave);
  ref.current = confirmLeave;
  useEffect(() => {
    if (!enabled) return;
    activeGuards.add(ref);
    return () => {
      activeGuards.delete(ref);
    };
  }, [enabled]);
}

/** 編集画面の標準ガード。dirty のときだけ離脱を確認し、戻り値は画面内の移動前に呼ぶ確認関数。 */
export function useLeaveGuard(isDirty: boolean): () => Promise<boolean> {
  const confirmLeave = useSettingsDraftGuard(isDirty, false, draftGuardMessages());
  useRegisterLeaveGuard(isDirty, confirmLeave);
  return confirmLeave;
}

/** 画面固有の確認文言を使うガード（例: 抽出確認の編集）。 */
export function useCustomLeaveGuard(enabled: boolean, confirmLeave: () => Promise<boolean>): void {
  useUnsavedChangesGuard(enabled, confirmLeave);
  useRegisterLeaveGuard(enabled, confirmLeave);
}

/** `navigate()` で移動する前に呼ぶ。dirty な画面がなければ即 true。確認は 1 回だけ出す。 */
export async function confirmPendingLeave(): Promise<boolean> {
  const [first] = activeGuards;
  return first ? first.current() : true;
}
