import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { t } from "../src/lib/i18n.ts";

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

test("DeepSec 検証結果は 5 件相当の最大高で縦スクロールする", () => {
  assert.match(
    entitlementsPanel,
    /role="region"[\s\S]*security\.deepsec\.resultListAriaLabel[\s\S]*tabIndex=\{0\}/u
  );
  assert.match(
    entitlementsPanel,
    /className="grid max-h-\[23\.25rem\] gap-2 overflow-y-auto overscroll-contain[^"]*"[\s\S]*data-testid="security-deepsec-verification-results"/u
  );
  assert.match(entitlementsPanel, /className="flex min-h-\[4\.25rem\] items-start/u);
});

test("DeepSec Data Grant editor は preview と独立した固定高 workspace を持つ", () => {
  assert.match(
    entitlementsPanel,
    /className="grid min-w-0 gap-3"[\s\S]*data-testid="security-deepsec-entitlement-form"/u
  );
  assert.doesNotMatch(
    entitlementsPanel,
    /grid-rows-\[auto_minmax\(0,1fr\)_auto\]/u
  );
  assert.match(
    entitlementsPanel,
    /className="h-\[36rem\] min-h-0 md:h-\[32rem\]"[\s\S]*data-testid="security-deepsec-entitlement-workspace-frame"/u
  );
  assert.match(
    entitlementsPanel,
    /className="[^"]*h-full[^"]*overflow-hidden[^"]*"[\s\S]*data-testid="security-deepsec-entitlement-workspace"/u
  );
  assert.match(
    entitlementsPanel,
    /className="[^"]*min-h-0[^"]*overflow-y-auto[^"]*"[\s\S]*data-testid="security-deepsec-entitlement-rules-list"/u
  );
  assert.match(
    entitlementsPanel,
    /className="min-h-0 overflow-y-auto pr-1"[\s\S]*ref=\{entitlementEditorScrollRef\}[\s\S]*security-deepsec-entitlement-rule-\$\{selectedEntitlementDraftIndex\}/u
  );
  assert.match(
    entitlementsPanel,
    /className="[^"]*border-t border-border[^"]*"[\s\S]*data-testid="security-deepsec-entitlement-action-region"/u
  );
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.emptyRulesTitle/u);
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.emptyRulesHint/u);
});

test("DeepSec object picker は native select ではなく50/50共通検索 picker を使う", () => {
  assert.match(entitlementsPanel, /<DeepSecTargetObjectPicker/u);
  assert.match(objectPicker, /role="listbox"/u);
  assert.match(objectPicker, /security-deepsec-object-picker-load-more/u);
  assert.match(objectPicker, /<DbObjectSearchOwnerFields/u);
  assert.doesNotMatch(objectPicker, /minmax\(8rem,13rem\)/u);
  assert.match(objectPicker, /dbAdmin\.search\.label/u);
  assert.match(objectPicker, /dbAdmin\.owner\.label/u);
  assert.equal(t("dbAdmin.search.placeholder"), "名前・コメントを入力");
  assert.equal(t("dbAdmin.ownerPrefix.placeholder"), "所有者の先頭を入力（例：ADM）");
  assert.doesNotMatch(entitlementsPanel, /<select[\s\S]*security\.deepsec\.entitlements\.resource/u);
});

test("DeepSec object picker footer は helper と load more を狭い幅で詰め込まない", () => {
  assert.match(
    objectPicker,
    /className="grid min-w-0 gap-2"[\s\S]*security\.deepsec\.entitlements\.oracleHelper[\s\S]*className="flex min-w-0 justify-end"[\s\S]*className="w-full min-w-0 justify-center lg:w-auto"/u
  );
});

test("DeepSec target objects wrapper は cursor paging と所有者 prefix・検索 scope を渡す", () => {
  assert.match(apiSource, /limit = 50/u);
  assert.match(apiSource, /params\.set\("q", q\.trim\(\)\)/u);
  assert.match(apiSource, /query_scope: "name_comment"/u);
  assert.match(apiSource, /params\.set\("owner_prefix", ownerPrefix\.trim\(\)\)/u);
  assert.doesNotMatch(apiSource, /params\.set\("owner", ownerPrefix\.trim\(\)\)/u);
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
  assert.match(entitlementsPanel, /className="mt-1 flex min-w-0 justify-start"/u);
  assert.match(entitlementsPanel, /className="mt-1 grid max-h-48 gap-2 overflow-auto/u);
  assert.match(pageSource, /useLayoutEffect\(\(\) => \{[\s\S]*pendingEntitlementScrollPositionsRef/u);
  assert.match(pageSource, /data-entitlement-scroll-container/u);
  assert.match(
    entitlementsPanel,
    /onSelectAll=\{\(\) =>[\s\S]*setEntitlementColumnsPreservingScroll\([\s\S]*availableColumnNames/u
  );
  assert.match(
    entitlementsPanel,
    /onClearAll=\{\(\) =>[\s\S]*setEntitlementColumnsPreservingScroll\(index, \[\]\)/u
  );
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

test("DeepSec Data Grant editor はロール全体を preview と apply に渡す", () => {
  const applyButtonBlock = sliceBetween(
    entitlementsPanel,
    'variant="danger"',
    'onClick={() => void handleApplyEntitlements()}'
  );

  assert.match(apiSource, /previewDeepSecDataEntitlements/u);
  assert.match(apiSource, /data-entitlements\/\$\{role\.role_id\}\/preview/u);
  assert.match(apiSource, /version: role\.version/u);
  assert.match(apiSource, /data_entitlements: dataEntitlementPayload\(role\)/u);
  assert.match(entitlementsPanel, /security\.deepsec\.entitlements\.generatePreview/u);
  assert.match(entitlementsPanel, /handlePreviewEntitlements/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview"/u);
  assert.match(entitlementsPanel, /max-h-\[min\(28rem,45dvh\)\]/u);
  assert.match(entitlementsPanel, /overflow-y-auto overscroll-contain/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview-toolbar"/u);
  assert.match(entitlementsPanel, /data-testid="security-deepsec-sql-preview-generate"/u);
  assert.match(entitlementsPanel, /className="w-full min-w-0 justify-center lg:w-auto"/u);
  assert.match(
    pageSource,
    /const handleApplyEntitlements = async \(\) => \{[\s\S]*securityApi\.applyDeepSecDataEntitlements\([\s\S]*data_entitlements: normalizedEntitlementDraftRows/u
  );
  assert.doesNotMatch(
    sliceBetween(pageSource, "const handleApplyEntitlements", "return ("),
    /updateDeepSecDataEntitlements/u
  );
  assert.doesNotMatch(apiSource, /entitlement_ids/u);
  assert.match(entitlementsPanel, /selectedRoleCleanupSql/u);
  assert.match(entitlementsPanel, /selectedRolePreviewRows\.map/u);
  assert.match(entitlementsPanel, /savedEntitlementRows\.length === 0/u);
  assert.match(pageSource, /type DataEntitlementDraft = DataEntitlement & \{ client_key: string \}/u);
  assert.match(entitlementsPanel, /key=\{entitlement\.client_key\}/u);
  assert.match(entitlementsPanel, /data-testid=\{`security-deepsec-entitlement-rule-tab-\$\{index\}`\}/u);
  assert.match(entitlementsPanel, /aria-pressed=\{selected\}/u);
  assert.match(entitlementsPanel, /setSelectedEntitlementDraftKey\(entitlement\.client_key\)/u);
  assert.doesNotMatch(entitlementsPanel, /<details[\s\S]*security-deepsec-entitlement-rule-\$\{index\}/u);
  assert.doesNotMatch(apiSource, /client_key/u);
  assert.match(entitlementsPanel, /targetKey \|\| t\("security\.deepsec\.entitlements\.ruleTitle"\)/u);
  assert.doesNotMatch(
    entitlementsPanel,
    /t\("security\.deepsec\.entitlements\.ruleTitle",\s*\{\s*index: index \+ 1/u
  );
  assert.doesNotMatch(applyButtonBlock, /normalizedEntitlementDraftRows\.length === 0/u);
  assert.doesNotMatch(pageSource, /handleSaveEntitlements/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.save"/u);
  assert.doesNotMatch(entitlementsPanel, /security\.deepsec\.entitlements\.saveHelper/u);
  assert.doesNotMatch(entitlementsPanel, /security\.common\.save/u);
});
