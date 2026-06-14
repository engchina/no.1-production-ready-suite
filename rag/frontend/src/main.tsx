import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { Providers } from "@/components/providers";
import "@/globals.css";

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
