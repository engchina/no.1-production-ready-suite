import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfirmProvider, Toaster } from "@production-ready/ui";
import { TraceDetailsLabelsProvider } from "@production-ready/ui";
import { initTheme } from "@production-ready/ui";
import {
  UnsavedChangesBlocker,
  useConfirmNavigationKey,
} from "@production-ready/system-settings";

import { App } from "./App";
import { ROUTER_BASENAME } from "@/lib/base-path";
import { installBrowserErrorGuards } from "@/lib/browser-error-guards";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";
import { AuthProvider } from "@/features/security/AuthProvider";
import { NL2SQL_TRACE_DETAILS_LABELS } from "@/features/nl2sql/chatProgress";
// フォント実体をビルドへ同梱し、Google Fonts / CDN に依存せず同一 origin から配信する。
import "@fontsource/noto-sans-jp/400.css";
import "@fontsource/noto-sans-jp/500.css";
import "@fontsource/noto-sans-jp/600.css";
import "@fontsource/noto-sans-jp/700.css";
// Noto Sans JP に無い簡体字・繁体字・香港の字だけを SC → TC → HK の順で補う（--font-sans / --font-mono の JP の後ろ。#3292）。
// <weight>.css は unicode-range で分けた face の集まりで、画面に出た字を含む分だけを読み込む。
import "@fontsource/noto-sans-sc/400.css";
import "@fontsource/noto-sans-sc/500.css";
import "@fontsource/noto-sans-sc/600.css";
import "@fontsource/noto-sans-sc/700.css";
import "@fontsource/noto-sans-tc/400.css";
import "@fontsource/noto-sans-tc/500.css";
import "@fontsource/noto-sans-tc/600.css";
import "@fontsource/noto-sans-tc/700.css";
import "@fontsource/noto-sans-hk/400.css";
import "@fontsource/noto-sans-hk/500.css";
import "@fontsource/noto-sans-hk/600.css";
import "@fontsource/noto-sans-hk/700.css";
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
initTheme(useUiStore);

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

const queryClient = new QueryClient();

/** 共有 ConfirmProvider に NL2SQL の文言を注入し、ルート遷移で開いている確認をキャンセルする。 */
function AppConfirmProvider({ children }: { children: ReactNode }) {
  const navigationKey = useConfirmNavigationKey();
  return (
    <ConfirmProvider
      labels={{ confirm: t("common.confirm"), cancel: t("common.cancel") }}
      navigationKey={navigationKey}
    >
      {children}
    </ConfirmProvider>
  );
}

/** 既存のルート定義（App の <Routes>）はそのまま使い、全体を data router の 1 つの splat route に載せる。 */
function RootLayout() {
  return (
    <AuthProvider>
      <AppConfirmProvider>
        {/* ブラウザの戻る/進むの blocker はアプリで 1 つ。各画面のガードはここへ未保存を登録する（#586）。 */}
        <UnsavedChangesBlocker />
        {/* 「トレースの詳細」の文言と段の名前は全画面で同じ（#3219）。 */}
        <TraceDetailsLabelsProvider labels={NL2SQL_TRACE_DETAILS_LABELS}>
          <App />
        </TraceDetailsLabelsProvider>
        <Toaster
          dismissLabel={t("common.dismiss")}
          regionLabel={t("common.notifications")}
        />
      </AppConfirmProvider>
    </AuthProvider>
  );
}

// data router にすると、共有の離脱ガードがブラウザの戻る/進むも確認できる（useBlocker。#138）。
// basename は配信の前置き（`/nl2sql/` で配信するときは `/nl2sql`。`/` なら undefined で今までどおり。#1316）。
const router = createBrowserRouter([{ path: "*", element: <RootLayout /> }], {
  basename: ROUTER_BASENAME,
});

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>
);
