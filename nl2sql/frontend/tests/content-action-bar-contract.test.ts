import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const componentSource = readFileSync(
  new URL("../src/components/ContentActionBar.tsx", import.meta.url),
  "utf8"
);
const dbObjectSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbObjectManagementShared.tsx", import.meta.url),
  "utf8"
);
const dbAdminSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbAdminShared.tsx", import.meta.url),
  "utf8"
);
const generatedSqlSource = readFileSync(
  new URL("../src/features/nl2sql/components/GeneratedSqlPanel.tsx", import.meta.url),
  "utf8"
);
const settingsPreviewSource = readFileSync(
  new URL("../src/components/settings/SettingsPreviewPanels.tsx", import.meta.url),
  "utf8"
);

test("ContentActionBar は内容内ツール操作を右寄せし、ARIA group を持つ", () => {
  assert.match(componentSource, /export function ContentActionBar/u);
  assert.match(componentSource, /ariaLabel/u);
  assert.match(componentSource, /role="group"/u);
  assert.match(componentSource, /aria-label=\{ariaLabel\}/u);
  assert.match(componentSource, /justify-end/u);
  assert.match(componentSource, /sm:justify-between/u);
});

test("ContentActionBar は左側情報と右側操作を分離できる", () => {
  for (const prop of ["leading", "title", "description", "meta"]) {
    assert.match(componentSource, new RegExp(prop, "u"));
  }
  assert.match(componentSource, /actionsClassName/u);
  assert.match(componentSource, /data-testid=\{testId\}/u);
});

test("DDL / SQL / preview の局所操作は ContentActionBar を使う", () => {
  assert.ok(dbObjectSource.includes('testId={`${idPrefix}-ddl-actions`}'));
  assert.match(dbAdminSource, /<ContentActionBar ariaLabel=\{t\("dbAdmin\.detail\.ddl"\)\}/u);
  assert.match(generatedSqlSource, /testId="generated-sql-content-actions"/u);
  assert.match(settingsPreviewSource, /testId="settings-preview-actions"/u);
});

test("DDL パネルは copy/download ボタンを手書き左寄せ flex に戻さない", () => {
  const dbObjectCopyIndex = dbObjectSource.indexOf('t("dbAdmin.detail.copy")');
  const dbAdminCopyIndex = dbAdminSource.indexOf('t("dbAdmin.detail.copy")');
  assert.notEqual(dbObjectCopyIndex, -1);
  assert.notEqual(dbAdminCopyIndex, -1);
  assert.doesNotMatch(
    dbObjectSource.slice(Math.max(0, dbObjectCopyIndex - 300), dbObjectCopyIndex),
    /flex flex-col gap-2 sm:flex-row sm:flex-wrap/u
  );
  assert.doesNotMatch(
    dbAdminSource.slice(Math.max(0, dbAdminCopyIndex - 300), dbAdminCopyIndex),
    /flex flex-wrap gap-2/u
  );
});
