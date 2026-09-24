import { Toaster as UiToaster } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

/**
 * Toast 表示領域。共有 UI パッケージの Toaster に RAG の i18n（閉じるラベル）を注入するラッパ。
 */
export function Toaster() {
  return <UiToaster dismissLabel={t("common.dismiss")} />;
}
