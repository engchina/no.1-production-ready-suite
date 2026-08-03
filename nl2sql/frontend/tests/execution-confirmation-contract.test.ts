import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

function sliceBetween(text: string, startMarker: string, endMarker: string): string {
  const start = text.indexOf(startMarker);
  assert.notEqual(start, -1);
  const end = text.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1);
  return text.slice(start, end);
}

test("ExecutionConfirmationField uses a stable neutral surface with a danger accent", () => {
  const component = sliceBetween(
    source("../src/features/nl2sql/components/DbAdminShared.tsx"),
    "export function ExecutionConfirmationField",
    "export function QueryResultsTable",
  );

  assert.match(component, /border border-border bg-background p-3/u);
  assert.match(component, /border-l-4 border-l-danger/u);
  assert.doesNotMatch(component, /bg-danger-bg\/70/u);
  assert.match(component, /border border-border bg-card/u);
  assert.match(component, /focus:border-danger focus:ring-2 focus:ring-danger\/40/u);
  assert.match(component, /focus:border-primary focus:ring-2 focus:ring-ring\/40/u);
});

test("ExecutionConfirmationField keeps empty, mismatch, and confirmed status tones distinct", () => {
  const component = sliceBetween(
    source("../src/features/nl2sql/components/DbAdminShared.tsx"),
    "export function ExecutionConfirmationField",
    "export function QueryResultsTable",
  );

  assert.match(component, /border-success\/30 bg-success-bg text-success/u);
  assert.match(component, /border-danger\/30 bg-danger-bg text-danger/u);
  assert.match(component, /border-border bg-card text-muted/u);
});

test("Drop object dialog does not wrap the confirmation field in a second danger surface", () => {
  const sourceText = source("../src/features/nl2sql/components/DbObjectManagementShared.tsx");

  assert.doesNotMatch(sourceText, /fieldset className="grid gap-3 rounded-md border border-danger\/30 bg-danger-bg\/70 p-3"/u);
  assert.match(sourceText, /fieldset className="grid gap-3 rounded-md border border-border bg-background p-3"/u);
});
