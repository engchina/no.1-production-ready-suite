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
const dbObjectFilterFieldsSource = readFileSync(
  new URL("../src/components/DbObjectFilterFields.tsx", import.meta.url),
  "utf8"
);
const incrementalQueriesSource = readFileSync(
  new URL("../src/features/nl2sql/incrementalQueries.ts", import.meta.url),
  "utf8"
);

test("データ管理プレビューは未取得行数を統計未取得として表示する", () => {
  assert.equal(t("dataMgmt.preview.rowUnknown"), "統計未取得");
  assert.match(
    dataManagementSource,
    /rowCount == null\) return t\("dataMgmt\.preview\.rowUnknown"\)/u
  );
});

test("データ管理プレビューは所有者前方一致入力と種別フィルタを表示し、行数フィルタを出さない", () => {
  assert.match(dataManagementSource, /ownerPrefixField=\{\{/u);
  assert.match(dataManagementSource, /t\("dbAdmin\.owner\.label"\)/u);
  assert.match(dataManagementSource, /t\("dbAdmin\.search\.label"\)/u);
  assert.match(dataManagementSource, /t\("dbAdmin\.search\.placeholder"\)/u);
  assert.match(dataManagementSource, /t\("dataMgmt\.preview\.kindFilter"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.rowFilter"\)/u);
});

test("対象画面の検索・所有者・種類フィルタを共通化する", () => {
  assert.equal(t("dbAdmin.search.label"), "検索");
  assert.equal(t("dbAdmin.search.placeholder"), "名前・コメントを入力");
  assert.equal(t("dbAdmin.owner.label"), "所有者");
  assert.equal(t("dbAdmin.ownerPrefix.placeholder"), "所有者の先頭を入力（例：ADM）");
  assert.match(dbObjectFilterFieldsSource, /export function DbManagementSearchField/u);
  assert.match(dbObjectFilterFieldsSource, /export function DbOwnerPrefixFilterField/u);
  assert.match(dbObjectFilterFieldsSource, /export function DbManagementSelectField/u);
  assert.match(dbObjectFilterFieldsSource, /export function DbObjectSearchOwnerFields/u);
  assert.match(dbObjectFilterFieldsSource, /md:grid-cols-2/u);
  assert.match(dbObjectFilterFieldsSource, /min-h-\[44px\]/u);
  assert.match(dbObjectFilterFieldsSource, /disabled:cursor-not-allowed/u);
  assert.match(dbObjectFilterFieldsSource, /focus:ring-2/u);
  assert.match(dbObjectFilterFieldsSource, /event\.currentTarget\.value\.toUpperCase\(\)/u);
  assert.match(dbObjectSharedSource, /from "@\/components\/DbObjectFilterFields"/u);
  assert.match(dbObjectSharedSource, /<DbObjectSearchOwnerFields/u);
  assert.match(dbObjectSharedSource, /ownerPrefixField && children/u);
  assert.match(
    dbObjectSharedSource,
    /md:grid-cols-2 xl:grid-cols-\[minmax\(0,1fr\)_minmax\(0,1fr\)_auto\] xl:items-end/u
  );
  assert.match(dataManagementSource, /<DbManagementSelectField[\s\S]{0,450}className="sm:w-48"/u);
  assert.match(metadataSqlManagementSource, /<DbManagementSelectField[\s\S]{0,450}className="sm:w-48"/u);
  assert.doesNotMatch(dbObjectSharedSource, /md:grid-cols-\[minmax\(0,1fr\)_13rem\]/u);
});

test("テーブル・ビュー管理は所有者前方一致入力と名前・コメント検索を使う", () => {
  assert.doesNotMatch(tableManagementSource, /useSchemaOwners/u);
  assert.doesNotMatch(viewManagementSource, /useSchemaOwners/u);
  assert.match(tableManagementSource, /debouncedTableOwnerPrefix/u);
  assert.match(viewManagementSource, /debouncedViewOwnerPrefix/u);
  assert.match(tableManagementSource, /useDbAdminObjects\([\s\S]{0,120}debouncedTableOwnerPrefix/u);
  assert.match(viewManagementSource, /useDbAdminObjects\([\s\S]{0,120}debouncedViewOwnerPrefix/u);
  assert.match(dbObjectSharedSource, /ownerPrefix: DbObjectOwnerPrefix/u);
  assert.doesNotMatch(dbObjectSharedSource, /ownerOptions/u);
  assert.doesNotMatch(dbObjectSharedSource, /ownerFilterAll/u);
  assert.doesNotMatch(dbObjectSharedSource, /ownerFilter: string/u);
  assert.match(tableManagementSource, /debouncedTableOwnerPrefix,\s*"name_comment"/u);
  assert.match(viewManagementSource, /debouncedViewOwnerPrefix,\s*"name_comment"/u);
  assert.match(
    incrementalQueriesSource,
    /params\.set\("owner_prefix", ownerPrefix\.trim\(\)\)/u
  );
  assert.match(
    incrementalQueriesSource,
    /params\.set\("query_scope", queryScope\)/u
  );
  assert.doesNotMatch(
    incrementalQueriesSource,
    /params\.set\("owner", ownerPrefix\.trim\(\)\)/u
  );
  assert.doesNotMatch(dbObjectSharedSource, /value="with_rows"/u);
  assert.doesNotMatch(dbObjectSharedSource, /value="empty_rows"/u);
});

test("コメント・アノテーション管理は共通所有者入力を種類フィルタの前に表示する", () => {
  const ownerFilterIndex = metadataSqlManagementSource.indexOf('t("dbAdmin.owner.label")');
  const typeFilterIndex = metadataSqlManagementSource.indexOf('t("metadataSql.targets.typeFilter")');
  assert.notEqual(ownerFilterIndex, -1);
  assert.notEqual(typeFilterIndex, -1);
  assert.ok(ownerFilterIndex < typeFilterIndex);
  assert.match(metadataSqlManagementSource, /ownerPrefixField=\{\{/u);
  assert.match(metadataSqlManagementSource, /targetOwnerPrefix/u);
  assert.match(
    metadataSqlManagementSource,
    /useDbAdminObjects\([\s\S]{0,140}debouncedTargetOwnerPrefix/u,
  );
  assert.match(metadataSqlManagementSource, /debouncedTargetOwnerPrefix,\s*"name_comment"/u);
});

test("データプレビューと COMMENT/ANNOTATION は name_comment scope を送る", () => {
  assert.match(dataManagementSource, /debouncedObjectOwnerPrefix,\s*"name_comment"/u);
  assert.match(metadataSqlManagementSource, /debouncedTargetOwnerPrefix,\s*"name_comment"/u);
});

test("データ管理プレビューは取得件数上限を指定でき、詳細条件入力を出さない", () => {
  assert.match(dataManagementSource, /const DEFAULT_DATA_PREVIEW_ROW_LIMIT = DEFAULT_SQL_ROW_LIMIT/u);
  assert.match(dataManagementSource, /useState\(String\(DEFAULT_DATA_PREVIEW_ROW_LIMIT\)\)/u);
  assert.match(dataManagementSource, /parseSqlRowLimit\(previewRowLimitInput\)/u);
  assert.match(dataManagementSource, /limit: rowLimit/u);
  assert.match(dataManagementSource, /<RowLimitField/u);
  assert.match(dataManagementSource, /<Play size=\{16\} aria-hidden="true" \/>/u);
  assert.match(dataManagementSource, /<X size=\{16\} aria-hidden="true" \/>/u);
  assert.match(dataManagementSource, /size="lg"[\s\S]*?\{t\("dataMgmt\.preview\.show"\)\}/u);
  assert.match(dataManagementSource, /size="lg"[\s\S]*?\{t\("dataMgmt\.preview\.clear"\)\}/u);
  assert.match(dataManagementSource, /onSelectPreviewObject=\{\(objectName\) => selectPreviewObject\(objectName, \{ manualSelection: true \}\)\}/u);
  assert.match(dataManagementSource, /setPreviewRowLimitInput\(String\(DEFAULT_DATA_PREVIEW_ROW_LIMIT\)\)/u);
  assert.match(dataManagementSource, /<QueryResultsTable results=\{preview\.results\} rowLimit=\{executedRowLimit\} \/>/u);
  assert.match(dataManagementSource, /where_clause: ""/u);
  assert.equal(t("queryResults.rowLimit.helper"), "0 は取得上限なし。");
  assert.equal(t("dataMgmt.preview.clear"), "クリア");
  assert.doesNotMatch(dataManagementSource, /const DATA_PREVIEW_ROW_LIMIT/u);
  assert.doesNotMatch(dataManagementSource, /limit: DATA_PREVIEW_ROW_LIMIT/u);
  assert.doesNotMatch(dataManagementSource, /dataMgmt\.preview\.fixedLimit/u);
  assert.doesNotMatch(dataManagementSource, /onSelect=\{\(item\) => onShowPreview\(item\.key\)\}/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.limit"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.where"\)/u);
  assert.doesNotMatch(dataManagementSource, /t\("dataMgmt\.preview\.wherePlaceholder"\)/u);
});

test("データ管理プレビューは対象選択から結果確認へのステップを表示する", () => {
  assert.equal(t("dataMgmt.preview.steps"), "テーブル・ビューデータ表示ステップ");
  assert.equal(t("dataMgmt.preview.stepTarget"), "対象選択");
  assert.equal(t("dataMgmt.preview.stepResults"), "結果確認");
  assert.match(dbObjectSharedSource, /topContent\?: ReactNode/u);
  assert.match(dbObjectSharedSource, /\{topContent\}[\s\S]{0,120}\{splitPaneId \?/u);
  assert.match(
    dataManagementSource,
    /topContent=\{\s*<DbObjectStepIndicator[\s\S]{0,260}dataTestId="data-preview-steps"/u
  );
  assert.match(dataManagementSource, /activeIndex=\{previewObject \? 1 : 0\}/u);
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
