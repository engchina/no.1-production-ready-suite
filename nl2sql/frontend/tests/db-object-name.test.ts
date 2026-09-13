import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  formatDbObjectName,
  formatEntitlementTargetName,
} from "../src/features/nl2sql/dbObjectIdentity.ts";
import { objectName } from "../src/features/nl2sql/ontology/physicalIdentity.ts";
import { schemaTableQualifiedName } from "../src/features/nl2sql/workbenchState.ts";

test("formatDbObjectName は OWNER.OBJECT を組み立て、Oracle の規則外の部分だけを引用する", () => {
  assert.equal(formatDbObjectName({ owner: "ADMIN", name: "DENPYO_ACTIVITY_LOG" }), "ADMIN.DENPYO_ACTIVITY_LOG");
  assert.equal(formatDbObjectName({ owner: "APP", name: "T$1#" }), "APP.T$1#");
  // カタログ値は大文字化しない（小文字・日本語・空白・先頭数字は引用識別子）
  assert.equal(formatDbObjectName({ owner: "APP", name: "lower" }), 'APP."lower"');
  assert.equal(formatDbObjectName({ owner: "APP", name: "MixedCase" }), 'APP."MixedCase"');
  assert.equal(formatDbObjectName({ owner: "APP", name: "売上" }), 'APP."売上"');
  assert.equal(formatDbObjectName({ owner: "Mixed Owner", name: "1ST" }), '"Mixed Owner"."1ST"');
  assert.equal(formatDbObjectName({ owner: "APP", name: 'A"B' }), 'APP."A""B"');
});

test("formatDbObjectName は owner が無いとき名前だけを返し、推測で補わない", () => {
  assert.equal(formatDbObjectName({ name: "ORDERS" }), "ORDERS");
  assert.equal(formatDbObjectName({ owner: "", name: "ORDERS" }), "ORDERS");
  assert.equal(formatDbObjectName({ owner: null, name: "ORDERS" }), "ORDERS");
  assert.equal(formatDbObjectName({ owner: "  ", name: "orders" }), '"orders"');
  assert.equal(formatDbObjectName({ owner: "APP", name: "" }), "");
});

test("formatDbObjectName は qualified_name を優先し、正規化した形で返す", () => {
  assert.equal(
    formatDbObjectName({ owner: "OTHER", name: "IGNORED", qualified_name: "APP.ORDERS" }),
    "APP.ORDERS"
  );
  assert.equal(formatDbObjectName({ name: "x", qualified_name: 'APP."売上"' }), 'APP."売上"');
  assert.equal(formatDbObjectName({ name: "x", qualified_name: '"Mixed Owner"."売上.2026"' }), '"Mixed Owner"."売上.2026"');
  // 空白だけの qualified_name は無視して owner / name から組み立てる
  assert.equal(formatDbObjectName({ owner: "APP", name: "ORDERS", qualified_name: "  " }), "APP.ORDERS");
});

test("formatDbObjectName は表示用のため壊れた引用でも例外を投げない", () => {
  assert.equal(formatDbObjectName({ name: "x", qualified_name: 'APP."BROKEN' }), 'APP."BROKEN');
  assert.equal(formatDbObjectName({ owner: "APP", name: '"BROKEN' }), 'APP."BROKEN');
});

test("formatDbObjectName は backend の qualified_object_name と同じ規則で組み立てる", () => {
  // backend/tests の object_identity と同じ入出力（format_object_part はカタログ値を大文字化しない）
  const cases: Array<[string, string, string]> = [
    ["APP", "ORDERS", "APP.ORDERS"],
    ["APP", "orders", 'APP."orders"'],
    ["APP", "売上", 'APP."売上"'],
    ["APP", "A B", 'APP."A B"'],
    ["APP", "_X", 'APP."_X"'],
  ];
  for (const [owner, name, expected] of cases) {
    assert.equal(formatDbObjectName({ owner, name }), expected);
  }
});

test("DeepSec のデータ権限キーは保存済みの大文字キーと一致し、引用名を大文字化で壊さない", () => {
  // 保存済みのデータ権限（backend が大文字化して保存）と一覧のカタログ値が同じキーになる
  assert.equal(
    formatEntitlementTargetName({ target_owner: "ADMIN", target_object: "DENPYO_ACTIVITY_LOG", resource_code: "" }),
    formatDbObjectName({ owner: "ADMIN", name: "DENPYO_ACTIVITY_LOG", qualified_name: "ADMIN.DENPYO_ACTIVITY_LOG" })
  );
  assert.equal(
    formatEntitlementTargetName({ target_owner: "admin", target_object: "orders", resource_code: "" }),
    "ADMIN.ORDERS"
  );
  // target_owner / target_object が無い旧データは resource_code から作る（従来の `.toUpperCase()` と同じ結果）
  assert.equal(formatEntitlementTargetName({ resource_code: "admin.orders" }), "ADMIN.ORDERS");
  assert.equal(formatEntitlementTargetName({ resource_code: "" }), "");
  // カタログの引用名は大文字化しないので、別の表（APP.MIXEDCASE）の権限と取り違えない
  assert.notEqual(
    formatDbObjectName({ owner: "APP", name: "MixedCase", qualified_name: "APP.MixedCase" }),
    formatEntitlementTargetName({ target_owner: "APP", target_object: "MIXEDCASE" })
  );
});

test("別実装だった修飾名の組み立てが formatDbObjectName に委ねられている", () => {
  assert.equal(
    schemaTableQualifiedName({
      table_name: "lower",
      logical_name: "lower",
      owner: "APP",
      table_type: "TABLE",
      comment: "",
      columns: [],
      constraints: [],
    }),
    'APP."lower"'
  );
  assert.equal(objectName({ owner: "APP", objectName: "売上", objectType: "table" }), 'APP."売上"');
  assert.equal(objectName({ owner: "", objectName: "ORDERS", objectType: "table" }), "ORDERS");

  const sources = [
    "src/features/nl2sql/workbenchState.ts",
    "src/features/nl2sql/metadataSql.ts",
    "src/features/nl2sql/ontology/physicalIdentity.ts",
    "src/features/nl2sql/ontology/nodeDisplay.ts",
    "src/features/security/SecurityDeepSecPage.tsx",
  ];
  for (const path of sources) {
    const source = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /`\$\{[\w.?]*owner(?:\.trim\(\))?\}\.\$\{[^}]*\}`/iu, `${path} で owner を単純連結している`);
    assert.doesNotMatch(source, /qualified_?[Nn]ame[^;\n]*\.toUpperCase\(\)/u, `${path} で修飾名を大文字化している`);
  }
});
