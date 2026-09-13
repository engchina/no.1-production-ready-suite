import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/features/nl2sql/components/DbAdminShared.tsx", import.meta.url),
  "utf8"
);
const dbObjectSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbObjectManagementShared.tsx", import.meta.url),
  "utf8"
);

function sliceBetween(text: string, startMarker: string, endMarker: string): string {
  const start = text.indexOf(startMarker);
  assert.notEqual(start, -1);
  const end = text.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1);
  return text.slice(start, end);
}

const workSection = sliceBetween(
  source,
  "export function WorkSection",
  "export function ManagementPanelShell"
);

test("WorkSection summary は mouse focus ring ではなく focus-visible を使う", () => {
  assert.match(workSection, /list-none/u);
  assert.match(workSection, /focus-visible:ring-2/u);
  assert.match(workSection, /focus-visible:ring-focus-ring/u);
  assert.match(workSection, /focus-visible:ring-danger-border/u);
  assert.doesNotMatch(workSection, /focus:ring-2/u);
  assert.doesNotMatch(workSection, /focus:ring-focus-ring/u);
});

test("WorkSection は既存折りたたみ UI と同じ chevron 表現を使う", () => {
  assert.match(workSection, /DisclosureChevron/u);
  assert.match(workSection, /expanded="group"/u);
  assert.match(workSection, /group\/disclosure/u);
  assert.match(workSection, /\[&::-webkit-details-marker\]:hidden/u);
});

test("DbAdminShared の details summary は普通の focus ring を使わない", () => {
  const summaryTags = source.match(/<summary\b[\s\S]*?>/gu) ?? [];
  assert.ok(summaryTags.length > 0);
  for (const summaryTag of summaryTags) {
    assert.doesNotMatch(summaryTag, /focus:ring-/u);
    assert.doesNotMatch(summaryTag, /focus:ring-2/u);
  }
});

test("管理画面のタブは共有 Tabs（WAI-ARIA Tabs のキー操作とフォーカス移動）に委ねる", () => {
  assert.match(source, /export function ManagementTabs/u);
  assert.match(source, /<Tabs\b/u);
  assert.match(dbObjectSource, /export function DbObjectManagementTabs/u);
  assert.match(dbObjectSource, /<Tabs\b/u);
  assert.doesNotMatch(source, /role="tab"/u);
  assert.doesNotMatch(dbObjectSource, /role="tab"/u);
});
