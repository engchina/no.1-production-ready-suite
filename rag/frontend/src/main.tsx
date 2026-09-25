import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { initTheme } from "@engchina/production-ready-ui";

import { App } from "./App";
import { Providers } from "@/components/providers";
import "@/fonts.css";
import "@/globals.css";
import { useUiStore } from "@/lib/ui-store";

// 永続化テーマを描画前に適用（FOUC 回避）＋ store / OS 設定の変更を購読する（#95）。
initTheme(useUiStore);

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

// 既存のルート定義（App の <Routes>）はそのまま、全体を data router の 1 つの splat route に載せる。
// data router にすると、共有の離脱ガードがブラウザの戻る/進むも確認できる（useBlocker。#138）。
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <Providers>
        <App />
      </Providers>
    ),
  },
]);

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
