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

function sliceFrom(text: string, startMarker: string): string {
  const start = text.indexOf(startMarker);
  assert.notEqual(start, -1);
  return text.slice(start);
}

test("ExecutionConfirmationField uses a stable neutral surface without a left danger accent", () => {
  const component = sliceBetween(
    source("../src/features/nl2sql/components/DbAdminShared.tsx"),
    "export function ExecutionConfirmationField",
    "export function QueryResultsTable",
  );

  assert.match(component, /border border-border bg-surface-sunken p-3/u);
  assert.doesNotMatch(component, /border-l-4 border-l-danger/u);
  assert.doesNotMatch(component, /bg-danger-subtle\/70/u);
  assert.match(component, /border border-border-control bg-surface/u);
  assert.match(component, /focus:border-danger-fg focus:ring-2 focus:ring-danger-border/u);
  assert.doesNotMatch(component, /tone\??:|isDanger/u);
});

test("ExecutionConfirmationField keeps empty, mismatch, and confirmed status tones distinct", () => {
  const component = sliceBetween(
    source("../src/features/nl2sql/components/DbAdminShared.tsx"),
    "export function ExecutionConfirmationField",
    "export function QueryResultsTable",
  );

  assert.match(component, /border-success-border bg-success-subtle text-success-fg/u);
  assert.match(component, /border-danger-border bg-danger-subtle text-danger-fg/u);
  assert.match(component, /border-border bg-surface text-fg-muted/u);
});

test("Drop object dialog does not wrap the confirmation field in a second danger surface", () => {
  const sourceText = source("../src/features/nl2sql/components/DbObjectManagementShared.tsx");
  const overlaySource = source("../src/components/ui/dialog-overlay.tsx");
  const component = sliceFrom(
    sourceText,
    "export function DropDbObjectDialog",
  );

  assert.doesNotMatch(sourceText, /fieldset className="grid gap-3 rounded-md border border-danger-border bg-danger-subtle\/70 p-3"/u);
  assert.match(component, /<DialogOverlayPortal className="p-3 sm:items-center">/u);
  assert.match(overlaySource, /createPortal/u);
  assert.match(overlaySource, /document\.body/u);
  assert.match(overlaySource, /fixed inset-0 z-\[var\(--z-dialog\)\]/u);
  assert.match(overlaySource, /bg-\[var\(--scrim\)\]/u);
  assert.match(component, /border border-border bg-surface-overlay shadow-\[var\(--shadow-dialog\)\]/u);
  assert.match(component, /border-b border-border bg-surface-overlay/u);
  assert.match(component, /border border-border bg-surface-sunken px-3 py-2/u);
  assert.match(component, /text-xs font-semibold text-fg/u);
  assert.match(component, /fieldset className="grid gap-3 rounded-md border border-border bg-surface-sunken p-3"/u);
  assert.match(component, /legend className="px-1 text-sm font-semibold text-fg"/u);
  assert.doesNotMatch(component, /border-l-4 border-l-danger/u);
  assert.doesNotMatch(component, /bg-danger-subtle/u);
});
