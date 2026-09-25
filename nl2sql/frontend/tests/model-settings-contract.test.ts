import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const settingsE2eSource = readFileSync(
  new URL("./e2e/nl2sql-system-settings.spec.ts", import.meta.url),
  "utf8"
);
// モデル設定の schema は3製品共通の pr_system_settings.model にある（#103）。
const backendSettingsSchemaSource = readFileSync(
  new URL(
    "../../../platform/packages/system_settings_backend/src/pr_system_settings/model.py",
    import.meta.url
  ),
  "utf8"
);

test("Enterprise AI model settings keep max output token fields in frontend contract", () => {
  for (const field of ["llm_max_output_tokens", "vlm_max_output_tokens"]) {
    assert.match(backendSettingsSchemaSource, new RegExp(`${field}: int`, "u"));
    assert.match(apiSource, new RegExp(`${field}: number;`, "u"));
    assert.match(settingsE2eSource, new RegExp(`${field}:`, "u"));
  }
});
