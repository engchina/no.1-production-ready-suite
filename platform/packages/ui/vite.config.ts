import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// ライブラリモード: React コンポーネント群を ESM で出力する。
// react / react-dom / react-router 等のランタイムは消費側に委ねる（peer/external）。
export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: {
        index: resolve(import.meta.dirname, "src/index.ts"),
        graph: resolve(import.meta.dirname, "src/graph.ts"),
        "graph-explorer": resolve(import.meta.dirname, "src/graph-explorer.ts"),
      },
      formats: ["es"],
      fileName: (_format, entryName) => `${entryName}.js`,
    },
    rollupOptions: {
      external: [
        "@tanstack/react-virtual",
        "@xyflow/react",
        "cytoscape",
        "cytoscape-fcose",
        "react",
        "react/jsx-runtime",
        "react-dom",
        "react-dom/client",
        "lucide-react",
        "zustand",
        "zustand/middleware",
      ],
    },
    sourcemap: true,
    emptyOutDir: true,
  },
});
