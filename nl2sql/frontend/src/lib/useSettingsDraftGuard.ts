import { useConfirm } from "@/components/ui/confirm-dialog";
import { t } from "@/lib/i18n";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";

/** 設定の編集と進行中の操作を離脱時に保護する。秘密情報は保存しない。 */
export function useSettingsDraftGuard(isDirty: boolean, busy: boolean) {
  const confirm = useConfirm();
  const confirmLeave = async () => !busy && (!isDirty || await confirm({
    title: t("settings.draft.discardTitle"),
    description: t("settings.draft.discardDescription"),
    confirmLabel: t("settings.draft.discardConfirm"),
    tone: "danger",
    dismissOnOverlay: false,
  }));
  useUnsavedChangesGuard(isDirty || busy, confirmLeave);
  return confirmLeave;
}
