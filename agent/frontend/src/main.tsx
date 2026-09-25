import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConfirmProvider, Toaster, initTheme } from "@engchina/production-ready-ui";

import { App } from "./App";
// globals.css が tailwindcss + 共有 tokens.css + @source を取り込む（単一エントリ）。
import "./globals.css";
// 書体は @fontsource の woff2 を同一 origin で配信する（外部 CDN に依存しない）。
import "./fonts.css";
import { useUiStore } from "@/lib/ui-store";

// 永続化テーマを描画前に適用（FOUC 回避）＋ store / OS 設定の変更を購読する（#95）。
initTheme(useUiStore);

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

const queryClient = new QueryClient();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider labels={{ confirm: "実行", cancel: "キャンセル" }}>
        <BrowserRouter>
          <App />
          <Toaster dismissLabel="閉じる" />
        </BrowserRouter>
      </ConfirmProvider>
    </QueryClientProvider>
  </StrictMode>
);
