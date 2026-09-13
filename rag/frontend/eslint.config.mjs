import js from "@eslint/js";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";

const browserGlobals = {
  AbortController: "readonly",
  Blob: "readonly",
  File: "readonly",
  FormData: "readonly",
  Headers: "readonly",
  URLSearchParams: "readonly",
  console: "readonly",
  document: "readonly",
  fetch: "readonly",
  localStorage: "readonly",
  navigator: "readonly",
  process: "readonly",
  setTimeout: "readonly",
  window: "readonly",
};

export default [
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**", "*.tsbuildinfo"],
  },
  js.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "vite.config.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: "latest",
        sourceType: "module",
      },
      globals: browserGlobals,
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooks,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "no-undef": "off",
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
  // デザインシステムの adherence ルール（platform の docs/design-system/adherence.oxlintrc.json 相当）。
  // 生の px は Tailwind のレイアウト幅（min-w-[980px] 等）で正当に使うため、inline style に限定する。
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]",
          message: "生の hex 色は使わない。@engchina/production-ready-ui の色トークン（bg-surface / text-fg-muted 等）を使う。",
        },
        {
          selector: "JSXAttribute[name.name='style'] Literal[value=/\\b\\d+px\\b/]",
          message: "inline style に生の px を書かない。余白・寸法トークンか Tailwind のユーティリティを使う。",
        },
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@engchina/production-ready-ui/*", "!@engchina/production-ready-ui/styles.css", "!@engchina/production-ready-ui/tokens.css"],
              message: "共有 UI パッケージはルート（@engchina/production-ready-ui）から import する。内部パスに依存しない。",
            },
          ],
        },
      ],
    },
  },
];
