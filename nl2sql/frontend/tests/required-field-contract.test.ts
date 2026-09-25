import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("required labels use the shared neutral RequiredBadge instead of an app-specific asterisk", () => {
  const component = source("../src/components/ui/required-field.tsx");

  assert.match(component, /import \{ RequiredBadge \} from "@engchina\/production-ready-ui"/u);
  assert.match(component, /requiredLabel = t\("common\.required"\)/u);
  // label は入力側の required / aria-required で伝えるのでバッジを読み上げない。legend は読み上げる。
  assert.match(component, /<RequiredBadge label=\{requiredLabel\} aria-hidden/u);
  assert.match(component, /<RequiredBadge label=\{requiredLabel\} className/u);
  assert.doesNotMatch(component, /RequiredIndicator|RequiredFieldsNote|requiredFieldsNote|text-danger-fg|>\s*\*\s*</u);
});

test("app source has no legacy asterisk required indicator or legend note", () => {
  const i18n = source("../src/lib/nl2sql-base-i18n.ts");
  assert.doesNotMatch(i18n, /common\.requiredFieldsNote/u);
  for (const path of [
    "../src/components/settings/ModelSettingsClient.tsx",
    "../src/features/nl2sql/pages/DataManagementPage.tsx",
    "../src/features/nl2sql/pages/ProfileManagementPage.tsx",
    "../src/features/security/AuthPages.tsx",
    "../src/features/security/SecurityDeepSecPage.tsx",
  ]) {
    assert.doesNotMatch(source(path), /RequiredIndicator|RequiredFieldsNote/u, path);
  }
});

test("file dropzone propagates required semantics to its native input", () => {
  const component = source("../src/components/ui/file-dropzone.tsx");

  assert.match(component, /<FieldLabel htmlFor=\{inputId\} label=\{label\} required=\{required\}/u);
  assert.match(component, /required=\{required\}/u);
  assert.match(component, /aria-required=\{required\}/u);
});

test("table import requires table, workbook sheet, file, and confirmation in the UI gate", () => {
  const page = source("../src/features/nl2sql/pages/TableManagementPage.tsx");

  assert.doesNotMatch(page, /RequiredFieldsNote/u);
  assert.match(page, /htmlFor="table-import-table-name"[\s\S]*required/u);
  assert.match(page, /htmlFor="table-import-sheet-name"[\s\S]*required=\{sheetRequired\}/u);
  assert.match(page, /<FileDropzone[\s\S]*label=\{t\("dataTools\.dbAdmin\.file"\)\}[\s\S]*required/u);
  assert.match(page, /fileReady[\s\S]*\(!sheetRequired \|\| Boolean\(sheet\.trim\(\)\)\)[\s\S]*isConfirmed/u);
});
