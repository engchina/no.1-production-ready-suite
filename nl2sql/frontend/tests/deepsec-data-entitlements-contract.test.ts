import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(
  new URL("../src/features/security/SecurityDeepSecPage.tsx", import.meta.url),
  "utf8"
);
const apiSource = readFileSync(
  new URL("../src/features/security/api.ts", import.meta.url),
  "utf8"
);

function sliceBetween(text: string, startMarker: string, endMarker: string): string {
  const start = text.indexOf(startMarker);
  assert.notEqual(start, -1);
  const end = text.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1);
  return text.slice(start, end);
}

const entitlementsPanel = pageSource.slice(
  pageSource.indexOf('{activeView === "data-permissions"')
);
const objectPicker = sliceBetween(
  pageSource,
  "function DeepSecTargetObjectPicker",
  "function DeepSecPlanSteps"
);

test("DeepSec 対象ロール list は 5 行相当の固定高 scroll container を使う", () => {
  assert.match(entitlementsPanel, /data-testid="security-deepsec-entitlement-roles"/u);
  assert.match(entitlementsPanel, /max-h-\[17\.5rem\]/u);
  assert.match(entitlementsPanel, /overflow-auto/u);
  assert.doesNotMatch(entitlementsPanel, /max-h-\[30rem\]/u);
});

test("DeepSec object picker は native select ではなく低い検索 picker を使う", () => {
  assert.match(entitlementsPanel, /<DeepSecTargetObjectPicker/u);
  assert.match(objectPicker, /role="listbox"/u);
  assert.match(objectPicker, /security-deepsec-object-picker-load-more/u);
  assert.match(objectPicker, /security\.deepsec\.entitlements\.objectOwner/u);
  assert.doesNotMatch(entitlementsPanel, /<select[\s\S]*security\.deepsec\.entitlements\.resource/u);
});

test("DeepSec object picker footer は helper と load more を狭い幅で詰め込まない", () => {
  assert.match(
    objectPicker,
    /className="grid min-w-0 gap-2"[\s\S]*security\.deepsec\.entitlements\.oracleHelper[\s\S]*className="flex min-w-0 justify-end"[\s\S]*className="w-full min-w-0 justify-center lg:w-auto"/u
  );
});

test("DeepSec target objects wrapper は cursor paging と owner filter を渡す", () => {
  assert.match(apiSource, /limit = 50/u);
  assert.match(apiSource, /params\.set\("q", q\.trim\(\)\)/u);
  assert.match(apiSource, /params\.set\("owner", owner\.trim\(\)\)/u);
  assert.match(apiSource, /params\.set\("cursor", cursor\)/u);
  assert.doesNotMatch(apiSource, /limit=100&type=all&row_state=all/u);
});

test("DeepSec Data Grant editor は必須表示を shared required-field components で統一する", () => {
  assert.match(pageSource, /FieldLabel, FieldLegend, RequiredIndicator/u);
  assert.match(objectPicker, /<RequiredIndicator \/>/u);
  assert.match(entitlementsPanel, /<FieldLabel[\s\S]*security\.deepsec\.entitlements\.scopeMode[\s\S]*required/u);
  assert.match(entitlementsPanel, /<FieldLegend[\s\S]*required[\s\S]*security\.deepsec\.entitlements\.columns/u);
  assert.match(entitlementsPanel, /<fieldset className="grid gap-2"/u);
});

test("DeepSec 許可列は一括選択バーと明示的な余白を持つ", () => {
  assert.match(pageSource, /import \{ BulkSelectionActions \} from "@\/components\/BulkSelectionActions"/u);
  assert.match(entitlementsPanel, /selectLabel=\{t\("common\.selection\.selectAll"\)\}/u);
  assert.match(entitlementsPanel, /clearLabel=\{t\("common\.selection\.clearAll"\)\}/u);
  assert.match(
    entitlementsPanel,
    /dataTestId=\{`security-deepsec-entitlement-column-selection-actions-\$\{index\}`\}/u
  );
  assert.match(
    entitlementsPanel,
    /data-testid=\{`security-deepsec-entitlement-columns-grid-\$\{index\}`\}/u
  );
  assert.match(entitlementsPanel, /className="mt-1 flex min-w-0 justify-end"/u);
  assert.match(entitlementsPanel, /className="mt-1 grid max-h-48 gap-2 overflow-auto/u);
  assert.match(entitlementsPanel, /onSelectAll=\{\(\) => setEntitlementColumns\(index, availableColumnNames\)\}/u);
  assert.match(entitlementsPanel, /onClearAll=\{\(\) => setEntitlementColumns\(index, \[\]\)\}/u);
});

test("DeepSec Data Grant editor は対象 object、許可列、行 scope の順に表示する", () => {
  const targetPickerIndex = entitlementsPanel.indexOf("<DeepSecTargetObjectPicker");
  const columnsIndex = entitlementsPanel.indexOf(
    "security.deepsec.entitlements.columns",
    targetPickerIndex
  );
  const scopeModeIndex = entitlementsPanel.indexOf(
    "security.deepsec.entitlements.scopeMode",
    columnsIndex
  );

  assert.notEqual(targetPickerIndex, -1);
  assert.notEqual(columnsIndex, -1);
  assert.notEqual(scopeModeIndex, -1);
  assert.ok(targetPickerIndex < columnsIndex);
  assert.ok(columnsIndex < scopeModeIndex);
});

test("DeepSec 条件 filter row は mobile/tablet で重ならない responsive grid を使う", () => {
  assert.match(
    entitlementsPanel,
    /className="grid min-w-0 gap-2 md:grid-cols-2 2xl:grid-cols-\[minmax\(15rem,1\.25fr\)_minmax\(9rem,0\.75fr\)_minmax\(10rem,0\.8fr\)_minmax\(12rem,1fr\)_auto\]"/u
  );
  assert.match(entitlementsPanel, /className=\{cn\(COMPACT_INPUT_CLASS, "min-w-0"\)\}/u);
  assert.match(entitlementsPanel, /className="justify-self-end self-end md:col-span-2 2xl:col-span-1"/u);
});

test("DeepSec 行 scope は 条件で制限 に列値制限とログインユーザーIDを統合する", () => {
  assert.match(pageSource, /const SCOPE_MODES = \["ALL", "FILTERS"\] as const/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.scopeColumnEquals/u);
  assert.doesNotMatch(entitlementsPanel, /<option value="COLUMN_EQUALS">/u);
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.scopeFilterValueSource/u);
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.scopeFilterValueLoginUserId/u);
  assert.match(pageSource, /LOGIN_USER_ID_SCOPE_VALUE_SOURCE = "LOGIN_USER_ID"/u);
  assert.match(entitlementsPanel, /value_source: event\.target\.value/u);
  assert.match(pageSource, /normalizeScopeFilterValueSource/u);
  assert.match(pageSource, /function scopeFilterSupportsValueSource\(operator: string, valueType: string\)/u);
  assert.match(pageSource, /\["TEXT", "NUMBER"\]\.includes\(valueType\)/u);
  assert.match(pageSource, /scopeFilterPositiveIntegerValidation/u);
});

test("DeepSec Data Grant editor は SQL preview と適用時の自動保存を分ける", () => {
  const applyButtonBlock = sliceBetween(
    entitlementsPanel,
    'variant="danger"',
    'onClick={() => void handleApplyEntitlements()}'
  );

  assert.match(apiSource, /previewDeepSecDataEntitlements/u);
  assert.match(apiSource, /data-entitlements\/\$\{roleId\}\/preview/u);
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.generatePreview/u);
  assert.match(entitlementsPanel, /handlePreviewEntitlements/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview"/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview-toolbar"/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview-generate"/u);
  assert.match(entitlementsPanel, /className="w-full min-w-0 justify-center lg:w-auto"/u);
  assert.match(
    pageSource,
    /const handleApplyEntitlements = async \(\) => \{[\s\S]*securityApi\.updateDeepSecDataEntitlements\(\{[\s\S]*data_entitlements: normalizedEntitlementDraftRows[\s\S]*securityApi\.applyDeepSecDataEntitlements/u
  );
  assert.match(entitlementsPanel, /<details[\s\S]*data-testid=\{`security-deepsec-entitlement-rule-\$\{index\}`\}/u);
  assert.match(entitlementsPanel, /targetKey \|\| t\("security\.deepsec\.entitlements\.ruleTitle"\)/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.ruleTitle"[\s\S]*index: index \+ 1/u);
  assert.doesNotMatch(applyButtonBlock, /normalizedEntitlementDraftRows\.length === 0/u);
  assert.doesNotMatch(pageSource, /handleSaveEntitlements/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.save"/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.saveHelper/u);
  assert.doesNotMatch(entitlementsPanel, /security\.common\.save/u);
});
