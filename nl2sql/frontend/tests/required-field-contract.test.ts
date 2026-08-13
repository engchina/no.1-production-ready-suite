import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("shared required indicator uses visible asterisk with screen-reader text", () => {
  const component = source("../src/components/ui/required-field.tsx");

  assert.match(component, /function RequiredIndicator/u);
  assert.match(component, />\s*\*\s*</u);
  assert.match(component, /className="sr-only"/u);
  assert.match(component, /t\("common\.required"\)/u);
  assert.match(component, /t\("common\.requiredFieldsNote"\)/u);
});

test("file dropzone propagates required semantics to its native input", () => {
  const component = source("../src/components/ui/file-dropzone.tsx");

  assert.match(component, /<FieldLabel htmlFor=\{inputId\} label=\{label\} required=\{required\}/u);
  assert.match(component, /required=\{required\}/u);
  assert.match(component, /aria-required=\{required\}/u);
});

test("table import requires table, workbook sheet, file, and confirmation in the UI gate", () => {
  const page = source("../src/features/nl2sql/pages/TableManagementPage.tsx");

  assert.match(page, /<RequiredFieldsNote \/>/u);
  assert.match(page, /htmlFor="table-import-table-name"[\s\S]*required/u);
  assert.match(page, /htmlFor="table-import-sheet-name"[\s\S]*required=\{sheetRequired\}/u);
  assert.match(page, /<FileDropzone[\s\S]*label=\{t\("dataTools\.dbAdmin\.file"\)\}[\s\S]*required/u);
  assert.match(page, /fileReady[\s\S]*\(!sheetRequired \|\| Boolean\(sheet\.trim\(\)\)\)[\s\S]*isConfirmed/u);
});
