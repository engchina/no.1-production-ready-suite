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

test("WorkSection summary のフォーカスはグローバルの :focus-visible（outline）に任せる（#355）", () => {
  assert.match(workSection, /list-none/u);
  // ring を足すとグローバルの outline と二重に表示される。outline を消すとキーボードの位置が見えなくなる。
  assert.doesNotMatch(workSection, /focus(?:-visible)?:ring/u);
  assert.doesNotMatch(workSection, /focus(?:-visible)?:outline-none/u);
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
    assert.doesNotMatch(summaryTag, /focus(?:-visible)?:ring-/u);
    assert.doesNotMatch(summaryTag, /focus(?:-visible)?:outline-none/u);
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
