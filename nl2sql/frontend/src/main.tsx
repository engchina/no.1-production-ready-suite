import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfirmProvider, Toaster } from "@engchina/production-ready-ui";

import { App } from "./App";
import { installBrowserErrorGuards } from "@/lib/browser-error-guards";
import { initTheme } from "@/lib/theme";
import { t } from "@/lib/i18n";
import { AuthProvider } from "@/features/security/AuthProvider";
// フォント実体をビルドへ同梱し、Google Fonts / CDN に依存せず同一 origin から配信する。
import "@fontsource/noto-sans-jp/400.css";
import "@fontsource/noto-sans-jp/500.css";
import "@fontsource/noto-sans-jp/600.css";
import "@fontsource/noto-sans-jp/700.css";
import "@fontsource/roboto/400.css";
import "@fontsource/roboto/500.css";
import "@fontsource/roboto/600.css";
import "@fontsource/roboto/700.css";
import "@fontsource/google-sans-code/400.css";
import "@fontsource/google-sans-code/500.css";
import "@fontsource/google-sans-code/600.css";
import "@fontsource/google-sans-code/700.css";
// globals.css が tailwindcss + 共有 tokens.css + @source を取り込む（単一エントリ）。
import "./globals.css";

// 永続化テーマを描画前に適用（FOUC 回避）＋ store/OS 変更を購読。
installBrowserErrorGuards();
initTheme();

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

const queryClient = new QueryClient();

/** 共有 ConfirmProvider に NL2SQL の文言を注入し、ルート遷移で開いている確認をキャンセルする。 */
function AppConfirmProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <ConfirmProvider
      labels={{ confirm: t("common.confirm"), cancel: t("common.cancel") }}
      navigationKey={location.key}
    >
      {children}
    </ConfirmProvider>
  );
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppConfirmProvider>
            <App />
            <Toaster
              dismissLabel={t("common.dismiss")}
              regionLabel={t("common.notifications")}
            />
          </AppConfirmProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
