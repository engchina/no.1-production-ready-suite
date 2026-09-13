import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

import adherence from "../../no.1-production-ready-platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };

const { rules: adherenceRules } = adherence.overrides[0];

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{js,mjs,cjs,ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  // デザインシステムの adherence ルール。正本は platform の docs/design-system/adherence.oxlintrc.json で、
  // ルールをコピーせず同じセレクタを ESLint 標準の no-restricted-syntax / no-restricted-imports に渡す
  // （platform AGENTS.md「lint」節）。CI は platform を sibling に checkout するので同じ相対パスで解決できる。
  // アプリ固有のルールを足す場合は、同じルール名で上書きせず別のルール名にする（adherence のセレクタが消える）。
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": adherenceRules["design-system/restricted-syntax"],
      "no-restricted-imports": adherenceRules["no-restricted-imports"],
    },
  },
);
