import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Toaster } from "@engchina/production-ready-ui";

import { App } from "./App";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";
// globals.css が tailwindcss + 共有 tokens.css + @source を取り込む（単一エントリ）。
import "./globals.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element が見つかりません。");
}

const queryClient = new QueryClient();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ConfirmProvider>
          <App />
          <Toaster dismissLabel="閉じる" />
        </ConfirmProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
