import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/components/ui/confirm-dialog.tsx", import.meta.url), "utf8");
const overlaySource = readFileSync(
  new URL("../src/components/ui/dialog-overlay.tsx", import.meta.url),
  "utf8"
);

test("ConfirmDialog is app-local and keeps the existing promise API", () => {
  assert.match(source, /const ConfirmContext = createContext<ConfirmFn \| null>\(null\)/u);
  assert.match(source, /export function useConfirm\(\): ConfirmFn/u);
  assert.match(source, /export function ConfirmProvider/u);
  assert.match(source, /new Promise<boolean>/u);
  assert.doesNotMatch(source, /UiConfirmProvider/u);
  assert.doesNotMatch(source, /export \{ useConfirm, type ConfirmOptions \} from "@engchina\/production-ready-ui"/u);
});

test("ConfirmDialog keeps the compact alert layout with DB object delete dialog surface tokens", () => {
  assert.match(source, /DialogOverlayPortal/u);
  assert.match(overlaySource, /createPortal/u);
  assert.match(overlaySource, /document\.body/u);
  assert.match(overlaySource, /bg-black\/60/u);
  assert.match(overlaySource, /fixed inset-0 z-50/u);
  assert.match(overlaySource, /data-testid=\{testId\}/u);
  assert.match(source, /max-w-md overflow-auto rounded-md border border-border bg-card shadow-xl/u);
  assert.match(source, /flex items-start gap-3 bg-card px-5 pt-5/u);
  assert.match(source, /rounded-full border bg-background \$\{iconClass\}/u);
  assert.match(source, /mt-5 flex justify-end gap-2 border-t border-border bg-background px-5 py-4/u);
  assert.match(source, /tone === "danger" \? "danger" : "primary"/u);
  assert.match(source, /const Icon = toneIcon\[tone\]/u);
  assert.match(source, /<Button variant="secondary" size="sm" onClick=\{onCancel\}>/u);
  assert.match(source, /<Button ref=\{confirmRef\} variant=\{confirmVariant\} size="sm" onClick=\{onConfirm\}>/u);
  assert.doesNotMatch(source, /border-l-danger/u);
  assert.doesNotMatch(source, /bg-danger-bg/u);
  assert.doesNotMatch(overlaySource, /z-\[1000\]/u);
  assert.doesNotMatch(source, /z-\[1000\]/u);
  assert.doesNotMatch(source, /command\.hint\.close/u);
});

test("ConfirmDialog keeps modal accessibility and escape behavior", () => {
  assert.match(source, /role="alertdialog"/u);
  assert.match(source, /aria-modal="true"/u);
  assert.match(source, /aria-labelledby=\{titleId\}/u);
  assert.match(source, /aria-describedby=\{description \? descriptionId : undefined\}/u);
  assert.match(source, /confirmRef\.current\?\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /previouslyFocused\.current\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /last\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /first\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /event\.key === "Escape"/u);
  assert.match(source, /event\.key !== "Tab"/u);
  assert.match(source, /dismissOnOverlay/u);
});
