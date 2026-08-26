import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/components/PageHeader.tsx", import.meta.url),
  "utf8",
);
const floatingSource = readFileSync(
  new URL("../src/components/FloatingMenu.tsx", import.meta.url),
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
  "../src/features/nl2sql/pages/SqlToQuestionPage.tsx",
  "../src/features/security/SecurityUsersPage.tsx",
  "../src/features/security/SecurityRolesPage.tsx",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

const pageHeaderStatusPages = [
  "../src/features/nl2sql/Nl2SqlWorkbench.tsx",
  "../src/features/nl2sql/pages/TableManagementPage.tsx",
  "../src/features/nl2sql/pages/ViewManagementPage.tsx",
  "../src/features/nl2sql/pages/DataManagementPage.tsx",
  "../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx",
  "../src/features/nl2sql/pages/ProfileManagementPage.tsx",
  "../src/features/nl2sql/pages/QuestionLearningPage.tsx",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));
const profileManagementPage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);

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

test("compact 操作メニューは lg 未満で 44px とキーボード・ARIA 契約を持つ", () => {
  assert.match(source, /h-\[44px\]/u);
  assert.match(source, /lg:flex/u);
  assert.match(source, /lg:hidden/u);
  assert.match(source, /aria-expanded=\{menuOpen\}/u);
  assert.match(source, /aria-controls=\{menuId\}/u);
  assert.match(source, /aria-haspopup="menu"/u);
  assert.match(floatingSource, /role="menu"/u);
  assert.match(source, /role=\{menuItem \? "menuitem"/u);
  for (const key of ["Escape", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.match(source, new RegExp(`event\\.key === "${key}"`, "u"));
  }
  assert.match(source, /firstEnabled\?\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /triggerRef\.current\?\.focus\(\{ preventScroll: true \}\)/u);
  assert.match(source, /items\[nextIndex\]\?\.focus\(\{ preventScroll: true \}\)/u);
});

test("compact 操作メニューは shared floating menu で viewport 内に配置する", () => {
  assert.match(source, /FloatingActionMenu/u);
  assert.doesNotMatch(source, /absolute right-0 top-full/u);
  assert.match(source, /menuRef\.current\?\.contains\(target\)/u);
  assert.match(floatingSource, /createPortal/u);
  assert.match(floatingSource, /data-floating-menu-placement/u);
  assert.match(floatingSource, /data-floating-menu-constrained/u);
  assert.match(floatingSource, /availableBelow/u);
  assert.match(floatingSource, /availableAbove/u);
  assert.match(floatingSource, /getBoundingClientRect/u);
  assert.match(floatingSource, /menu\.scrollHeight \+ menuBorderHeight/u);
  assert.match(floatingSource, /constrained \? \{ maxHeight/u);
  assert.match(floatingSource, /position\?\.constrained && "overflow-y-auto overscroll-contain"/u);
  assert.doesNotMatch(floatingSource, /"fixed[^"]*overflow-y-auto/u);
});

test("ページ操作は作業系・ツール系・危険操作を kind から分組する", () => {
  assert.match(source, /type PageActionGroup = "task" \| "utility" \| "danger"/u);
  assert.match(source, /function actionGroup/u);
  assert.match(source, /data-page-action-group=\{group\}/u);
  assert.match(source, /data-page-action-group-start=\{startsGroup \? "true"/u);
  assert.match(source, /border-l border-border pl-4/u);
  assert.match(source, /border-t border-border pt-1/u);
});

test("ページ操作はボタンの loading のみを担当し、詳細な処理表示を自動配置しない", () => {
  assert.doesNotMatch(source, /ProcessingIndicator/u);
  assert.doesNotMatch(source, /loadingAction/u);
  assert.doesNotMatch(source, /data-processing-placement/u);
});

test("PageHeaderStatusBadge は短いページ状態を live region として公開する", () => {
  assert.match(source, /export function PageHeaderStatusBadge/u);
  assert.match(source, /role="status"/u);
  assert.match(source, /aria-live="polite"/u);
  assert.match(source, /aria-atomic="true"/u);
  assert.match(source, /data-page-header-status="true"/u);
  assert.match(source, /<StatusBadge variant=\{variant\} label=\{label\}/u);
});

test("ヘッダー横の状態 badge は共通 PageHeaderStatusBadge を使う", () => {
  for (const page of pageHeaderStatusPages) {
    assert.match(page, /PageHeaderStatusBadge/u);
    assert.doesNotMatch(page, /<span[^>]+aria-live="polite"[^>]*>\s*<StatusBadge/u);
  }
});

test("refresh 系の完了状態はヘッダー横 badge に残さない", () => {
  for (const page of pageHeaderStatusPages.slice(0, 5)) {
    assert.match(page, /!== "done"/u);
  }
  assert.match(profileManagementPage, /dbProfileRefreshStatus === "error"/u);
  assert.match(profileManagementPage, /schemaRefreshStatus === "error"/u);
  assert.doesNotMatch(profileManagementPage, /headerRefreshStatus === "done"/u);
});

test("業務プロファイルの schema / DB Profile refresh ボタンは loading と disabled を揃える", () => {
  assert.match(profileManagementPage, /id: "schema-refresh"/u);
  assert.match(profileManagementPage, /loading: schemaRefreshing \|\| startSchemaRefresh\.isPending/u);
  assert.match(profileManagementPage, /disabled: schemaRefreshing \|\| startSchemaRefresh\.isPending/u);
  assert.match(profileManagementPage, /id: "db-profile-refresh"/u);
  assert.match(profileManagementPage, /loading: dbProfileRefreshing \|\| startDbProfileRefresh\.isPending/u);
  assert.match(profileManagementPage, /disabled: dbProfileRefreshing \|\| startDbProfileRefresh\.isPending/u);
});

test("移行対象ページはローカル PageHeader を使い、旧トップ概覧カードを表示しない", () => {
  for (const page of migratedPages) {
    assert.match(page, /from "@\/components\/PageHeader"/u);
    assert.doesNotMatch(page, /<DbObjectManagementStatusBar/u);
    assert.doesNotMatch(page, /<DbObjectStatusBar/u);
    assert.doesNotMatch(page, /<SecurityManagementStatusBar/u);
  }
});
