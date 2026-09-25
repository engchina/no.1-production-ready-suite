import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
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

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Providers>
        <App />
      </Providers>
    </BrowserRouter>
  </StrictMode>
);
