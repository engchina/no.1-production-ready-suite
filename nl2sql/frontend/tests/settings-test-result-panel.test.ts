import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/components/settings/SettingsTestResultPanel.tsx", import.meta.url),
  "utf8"
);

test("settings test result panel defines the three semantic result tones", () => {
  assert.match(source, /export type SettingsTestResultTone = "success" \| "warning" \| "danger"/u);
  assert.match(source, /<Banner severity=\{tone\} title=\{message\}>/u);
  assert.doesNotMatch(source, /TONE_STYLE|border-danger\/30|bg-danger-bg/u);
});

test("settings test result panel exposes accessible status and error behavior", () => {
  assert.match(source, /<Banner severity=\{tone\} title=\{message\}>/u);
  assert.match(source, /data-settings-test-result=""/u);
  assert.doesNotMatch(source, /role="alert"|role=\{/u);
});

test("settings test result panel keeps responsive details and failure recovery diagnostics", () => {
  assert.match(source, /sm:grid-cols-2/u);
  assert.match(source, /settings\.testResult\.elapsed/u);
  assert.match(source, /settings\.testResult\.checkedAt/u);
  assert.match(source, /settings\.testResult\.troubleshooting/u);
  assert.match(source, /tone !== "success" && troubleshooting\.length > 0/u);
  assert.match(source, /tone === "danger" && errorType/u);
  assert.match(source, /<StatusBadge variant="danger" label=\{errorType\} \/>/u);
  assert.doesNotMatch(source, /\{rawError\}|<pre/u);
});

test("settings test result details omit unavailable values without dropping false and zero", () => {
  assert.match(
    source,
    /value === null \|\| value === undefined \|\| value === "" \? \[\] : \[\{ label, value \}\]/u
  );
});
