import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    // 部品を描くテスト（AnswerText など）で、共有 UI と同じ React と lucide-react を使う（Agent の vitest.config.ts と同じ。
    // lucide-react 1.x のアイコンは React の context を読む。#5571）。
    dedupe: ["react", "react-dom", "lucide-react"],
    alias: { "@": resolve(import.meta.dirname, "src") },
  },
});
