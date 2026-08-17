import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { t } from "../src/lib/i18n.ts";

const dataManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/DataManagementPage.tsx", import.meta.url),
  "utf8"
);
const tableManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/TableManagementPage.tsx", import.meta.url),
  "utf8"
);
const viewManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/ViewManagementPage.tsx", import.meta.url),
  "utf8"
);
const metadataSqlManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx", import.meta.url),
  "utf8"
);
const dbObjectSharedSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbObjectManagementShared.tsx", import.meta.url),
  "utf8"
);

test("データ管理プレビューは未取得行数を統計未取得として表示する", () => {
  assert.equal(t("dataMgmt.preview.rowUnknown"), "統計未取得");
  assert.match(
    dataManagementSource,
    /rowCount == null\) return t\("dataMgmt\.preview\.rowUnknown"\)/u
  );
});

test("データ管理プレビューは所有者フィルタと種別フィルタを表示し、行数フィルタを出さない", () => {
  assert.match(dataManagementSource, /t\("dataMgmt\.preview\.ownerFilter"\)/u);
  assert.match(dataManagementSource, /t\("dataMgmt\.preview\.kindFilter"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.rowFilter"\)/u);
});

test("テーブル・ビュー管理は所有者フィルタを表示し、行数フィルタを出さない", () => {
  assert.equal(t("tableMgmt.toolbar.filter"), "所有者フィルタ");
  assert.equal(t("viewMgmt.toolbar.filter"), "所有者フィルタ");
  assert.match(tableManagementSource, /useSchemaOwners/u);
  assert.match(viewManagementSource, /useSchemaOwners/u);
  assert.match(tableManagementSource, /useDbAdminObjects\(debouncedTableSearch, "table", "all", tableOwnerQuery\)/u);
  assert.match(viewManagementSource, /useDbAdminObjects\(debouncedViewSearch, "view", "all", viewOwnerQuery\)/u);
  assert.match(dbObjectSharedSource, /ownerOptions: string\[\]/u);
  assert.doesNotMatch(dbObjectSharedSource, /value="with_rows"/u);
  assert.doesNotMatch(dbObjectSharedSource, /value="empty_rows"/u);
});

test("コメント・アノテーション管理は所有者フィルタを種類フィルタの前に表示する", () => {
  assert.equal(t("metadataSql.targets.ownerFilter"), "所有者フィルタ");
  const ownerFilterIndex = metadataSqlManagementSource.indexOf('t("metadataSql.targets.ownerFilter")');
  const typeFilterIndex = metadataSqlManagementSource.indexOf('t("metadataSql.targets.typeFilter")');
  assert.notEqual(ownerFilterIndex, -1);
  assert.notEqual(typeFilterIndex, -1);
  assert.ok(ownerFilterIndex < typeFilterIndex);
  assert.match(metadataSqlManagementSource, /targetOwnerFilter/u);
  assert.match(metadataSqlManagementSource, /useDbAdminObjects\(debouncedTargetSearch, targetFilter, "all", targetOwnerQuery\)/u);
});

test("データ管理プレビューは表示件数を10件固定にし、詳細条件入力を出さない", () => {
  assert.match(dataManagementSource, /const DATA_PREVIEW_ROW_LIMIT = 10/u);
  assert.match(dataManagementSource, /limit: DATA_PREVIEW_ROW_LIMIT/u);
  assert.match(dataManagementSource, /where_clause: ""/u);
  assert.match(dataManagementSource, /t\("dataMgmt\.preview\.fixedLimit", \{ count: DATA_PREVIEW_ROW_LIMIT \}\)/u);
  assert.equal(t("dataMgmt.preview.fixedLimit", { count: 10 }), "表示件数 10 件固定");
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.limit"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.where"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.wherePlaceholder"\)/u);
});

test("データ管理の対象ピッカーはヘッダーソート契約を持つ", () => {
  assert.match(dbObjectSharedSource, /export type DbObjectPickerSortKey = "name" \| "kind" \| "row_count" \| "owner"/u);
  assert.match(dbObjectSharedSource, /export interface DbObjectPickerSortState/u);
  assert.match(dbObjectSharedSource, /rowCount\?: number \| null/u);
  assert.match(dbObjectSharedSource, /aria-sort=\{ariaSort\}/u);
  assert.match(dbObjectSharedSource, /onSortChange\?: \(key: DbObjectPickerSortKey\) => void/u);
  assert.match(dataManagementSource, /previewObjectSort/u);
  assert.match(dataManagementSource, /csvTableSort/u);
  assert.equal(t("objectSelector.sort.button", { label: "対象名", direction: "昇順" }), "対象名: 昇順");
});
