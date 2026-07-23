import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/components/PageHeader.tsx", import.meta.url),
  "utf8",
);

const migratedPages = [
  "../src/features/nl2sql/pages/TableManagementPage.tsx",
  "../src/features/nl2sql/pages/ViewManagementPage.tsx",
  "../src/features/nl2sql/pages/DataManagementPage.tsx",
  "../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx",
  "../src/features/nl2sql/pages/ProfileManagementPage.tsx",
  "../src/features/nl2sql/pages/HistoryPage.tsx",
  "../src/features/nl2sql/pages/FeedbackManagementPage.tsx",
  "../src/features/nl2sql/pages/QuestionLearningPage.tsx",
  "../src/features/nl2sql/pages/SampleDataPage.tsx",
  "../src/features/nl2sql/pages/SqlAnalysisPage.tsx",
  "../src/features/nl2sql/pages/SqlToQuestionPage.tsx",
  "../src/features/security/SecurityUsersPage.tsx",
  "../src/features/security/SecurityRolesPage.tsx",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

test("PageAction descriptor と固定優先順位をローカル実装が保持する", () => {
  assert.match(source, /export interface PageAction/u);
  for (const field of [
    "id",
    "kind",
    "label",
    "icon",
    "onClick",
    "loading",
    "disabled",
    "testId",
  ]) {
    assert.match(source, new RegExp(`\\b${field}\\??:`, "u"));
  }
  assert.match(source, /primary:\s*0/u);
  assert.match(source, /secondary:\s*1/u);
  assert.match(source, /utility:\s*2/u);
  assert.match(source, /danger:\s*3/u);
  assert.match(source, /left\.index - right\.index/u);
});

test("モバイル操作メニューは 44px とキーボード・ARIA 契約を持つ", () => {
  assert.match(source, /h-\[44px\]/u);
  assert.match(source, /aria-expanded=\{menuOpen\}/u);
  assert.match(source, /aria-controls=\{menuId\}/u);
  assert.match(source, /aria-haspopup="menu"/u);
  assert.match(source, /role="menu"/u);
  assert.match(source, /role=\{menuItem \? "menuitem"/u);
  for (const key of ["Escape", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.match(source, new RegExp(`event\\.key === "${key}"`, "u"));
  }
  assert.match(source, /triggerRef\.current\?\.focus/u);
});

test("移行対象ページはローカル PageHeader を使い、旧トップ概覧カードを表示しない", () => {
  for (const page of migratedPages) {
    assert.match(page, /from "@\/components\/PageHeader"/u);
    assert.doesNotMatch(page, /<DbObjectManagementStatusBar/u);
    assert.doesNotMatch(page, /<DbObjectStatusBar/u);
    assert.doesNotMatch(page, /<SecurityManagementStatusBar/u);
  }
});
