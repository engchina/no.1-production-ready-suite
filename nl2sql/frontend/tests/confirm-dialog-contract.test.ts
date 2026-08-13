import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/components/ui/confirm-dialog.tsx", import.meta.url), "utf8");

test("ConfirmDialog is app-local and keeps the existing promise API", () => {
  assert.match(source, /const ConfirmContext = createContext<ConfirmFn \| null>\(null\)/u);
  assert.match(source, /export function useConfirm\(\): ConfirmFn/u);
  assert.match(source, /export function ConfirmProvider/u);
  assert.match(source, /new Promise<boolean>/u);
  assert.doesNotMatch(source, /UiConfirmProvider/u);
  assert.doesNotMatch(source, /export \{ useConfirm, type ConfirmOptions \} from "@engchina\/production-ready-ui"/u);
});

test("ConfirmDialog uses a neutral confirmation surface with tone accents only", () => {
  assert.match(source, /border border-border border-l-4 bg-card shadow-xl/u);
  assert.match(source, /border-l-danger/u);
  assert.match(source, /bg-card px-5 pt-5/u);
  assert.match(source, /border bg-background \$\{iconClass\}/u);
  assert.match(source, /border-t bg-background px-5 py-4/u);
  assert.match(source, /tone === "danger" \? "danger" : "primary"/u);
  assert.doesNotMatch(source, /bg-danger-bg/u);
});

test("ConfirmDialog keeps modal accessibility and escape behavior", () => {
  assert.match(source, /role="alertdialog"/u);
  assert.match(source, /aria-modal="true"/u);
  assert.match(source, /aria-labelledby=\{titleId\}/u);
  assert.match(source, /aria-describedby=\{description \? descriptionId : undefined\}/u);
  assert.match(source, /confirmRef\.current\?\.focus\(\)/u);
  assert.match(source, /previouslyFocused\.current\.focus\(\)/u);
  assert.match(source, /event\.key === "Escape"/u);
  assert.match(source, /event\.key !== "Tab"/u);
  assert.match(source, /dismissOnOverlay/u);
});
