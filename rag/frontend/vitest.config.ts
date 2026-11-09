import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    // 部品を描くテスト（AnswerText など）で、共有 UI と同じ React を使う（Agent の vitest.config.ts と同じ）。
    dedupe: ["react", "react-dom"],
    alias: { "@": resolve(import.meta.dirname, "src") },
  },
});
