import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { applySchemaBulkSelection } from "../src/features/nl2sql/profileObjectSelection.ts";

const profilePage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);
const incrementalQueries = readFileSync(
  new URL("../src/features/nl2sql/incrementalQueries.ts", import.meta.url),
  "utf8",
);

test("フィルタ未適用のスキーマ全選択はスキーマ全体を置き換える", () => {
  const next = applySchemaBulkSelection({
    current: ["SALES.ORDERS", "HR.EMPLOYEES"],
    snapshot: ["SALES.ORDERS", "SALES.ITEMS"],
    ownerPrefix: "SALES.",
    select: true,
    filtered: false,
  });
  assert.deepEqual(next, ["HR.EMPLOYEES", "SALES.ORDERS", "SALES.ITEMS"]);
});

test("フィルタ未適用のスキーマ全解除はそのスキーマだけを外す", () => {
  const next = applySchemaBulkSelection({
    current: ["SALES.ORDERS", "HR.EMPLOYEES"],
    snapshot: [],
    ownerPrefix: "SALES.",
    select: false,
    filtered: false,
  });
  assert.deepEqual(next, ["HR.EMPLOYEES"]);
});

test("フィルタ適用中の全選択はヒットした object 以外の選択を広げない", () => {
  const next = applySchemaBulkSelection({
    current: ["SALES.ORDERS"],
    snapshot: ["SALES.ORDER_ITEMS"],
    ownerPrefix: "SALES.",
    select: true,
    filtered: true,
  });
  // フィルタ外の SALES.ORDERS は保持され、スキーマ内の未ヒット object は追加されない。
  assert.deepEqual(next, ["SALES.ORDERS", "SALES.ORDER_ITEMS"]);
});

test("フィルタ適用中の全解除はヒットした object だけを外す", () => {
  const next = applySchemaBulkSelection({
    current: ["SALES.ORDERS", "SALES.ORDER_ITEMS", "HR.EMPLOYEES"],
    snapshot: ["SALES.ORDER_ITEMS"],
    ownerPrefix: "SALES.",
    select: false,
    filtered: true,
  });
  assert.deepEqual(next, ["SALES.ORDERS", "HR.EMPLOYEES"]);
});

test("引用符付き・小文字の保存値も同じキーとして突合する", () => {
  const next = applySchemaBulkSelection({
    current: ['"SALES"."ORDERS"', "sales.items"],
    snapshot: ["SALES.ORDERS"],
    ownerPrefix: "sales.",
    select: false,
    filtered: true,
  });
  assert.deepEqual(next, ["sales.items"]);
});

test("スキーマ一括操作は検索フィルタをサーバ問い合わせへ伝搬する", () => {
  assert.match(profilePage, /applySchemaBulkSelection\(\{/u);
  assert.match(
    profilePage,
    /getSchemaObjectSnapshot\(owner, kind === "table" \? "TABLE" : "VIEW", filter\)/u,
  );
  assert.match(incrementalQueries, /q: normalizedQuery,/u);
  // フィルタ適用中は解除側も snapshot 範囲に限定する(スキーマ全体へ波及させない)。
  assert.match(profilePage, /select \|\| filtered/u);
});

test("表示一覧はサーバ検索結果をそのまま使い二重フィルタしない", () => {
  assert.doesNotMatch(profilePage, /filterProfileObjects/u);
  assert.match(profilePage, /tableObjects=\{tableObjects\}/u);
  assert.match(profilePage, /viewObjects=\{viewObjects\}/u);
});

test("業務プロファイル画面の検索は共有デバウンス hook を通す", () => {
  const debounceHook = readFileSync(
    new URL("../src/lib/useDebouncedValue.ts", import.meta.url),
    "utf8",
  );
  assert.match(debounceHook, /export function useDebouncedValue<T>/u);
  assert.match(debounceHook, /export const LIST_SEARCH_DEBOUNCE_MS = 250;/u);

  assert.match(
    profilePage,
    /useDebouncedValue\(profileSearch, LIST_SEARCH_DEBOUNCE_MS\)/u,
  );
  assert.match(profilePage, /useDebouncedValue\(objectFilter, LIST_SEARCH_DEBOUNCE_MS\)/u);
  assert.match(profilePage, /useSchemaObjects\(debouncedObjectFilter, "TABLE"\)/u);
  assert.match(profilePage, /useSchemaObjects\(debouncedObjectFilter, "VIEW"\)/u);
  // 一括操作も表示中の一覧と同じ条件を使う。
  assert.match(profilePage, /const filter = debouncedObjectFilter\.trim\(\);/u);
});

test("管理系一覧ページはデバウンス hook を重複定義しない", () => {
  for (const page of [
    "TableManagementPage",
    "ViewManagementPage",
    "MetadataSqlManagementPage",
    "DataManagementPage",
  ]) {
    const source = readFileSync(
      new URL(`../src/features/nl2sql/pages/${page}.tsx`, import.meta.url),
      "utf8",
    );
    assert.doesNotMatch(source, /(const|function) useDebouncedValue/u, page);
    assert.match(source, /from "@\/lib\/useDebouncedValue"/u, page);
  }
});
