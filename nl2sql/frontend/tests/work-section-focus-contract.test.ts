import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/features/nl2sql/components/DbAdminShared.tsx", import.meta.url),
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
  "export function focusManagementTabElement"
);

test("WorkSection summary は mouse focus ring ではなく focus-visible を使う", () => {
  assert.match(workSection, /list-none/u);
  assert.match(workSection, /focus-visible:ring-2/u);
  assert.match(workSection, /focus-visible:ring-ring\/40/u);
  assert.match(workSection, /focus-visible:ring-danger\/40/u);
  assert.doesNotMatch(workSection, /focus:ring-2/u);
  assert.doesNotMatch(workSection, /focus:ring-ring\/40/u);
});

test("WorkSection は既存折りたたみ UI と同じ chevron 表現を使う", () => {
  assert.match(workSection, /ChevronDown/u);
  assert.match(workSection, /group-open:rotate-180/u);
  assert.match(workSection, /motion-reduce:transition-none/u);
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
