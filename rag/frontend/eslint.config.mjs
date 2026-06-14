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
];
