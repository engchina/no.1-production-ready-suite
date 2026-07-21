import type { ReactNode } from "react";

import { ConfirmProvider as UiConfirmProvider } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

// useConfirm / 型は共有 UI パッケージをそのまま再公開。
export { useConfirm, type ConfirmOptions } from "@engchina/production-ready-ui";

/**
 * 確認ダイアログ Provider。共有 UI パッケージへ NL2SQL の i18n（既定文言）を注入する。
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  return (
    <UiConfirmProvider
      labels={{ confirm: t("common.confirm"), cancel: t("common.cancel") }}
    >
      {children}
    </UiConfirmProvider>
  );
}
