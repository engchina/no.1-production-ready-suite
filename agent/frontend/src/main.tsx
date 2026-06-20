import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { Toaster } from "@engchina/production-ready-ui";

import { App } from "./App";
// globals.css が tailwindcss + 共有 tokens.css + @source を取り込む（単一エントリ）。
import "./globals.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
      <Toaster dismissLabel="閉じる" />
    </BrowserRouter>
  </StrictMode>
);
