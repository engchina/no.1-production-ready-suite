import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// ライブラリモード: 共有システム設定画面を ESM で出力する。
// React と共有 UI（@engchina/production-ready-ui）は消費側のものを使う（external）。
export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: resolve(__dirname, "src/index.ts"),
      formats: ["es"],
      fileName: () => "index.js",
    },
    rollupOptions: {
      external: ["react", "react/jsx-runtime", "react-dom", "@engchina/production-ready-ui"],
    },
    sourcemap: true,
    emptyOutDir: true,
  },
});
