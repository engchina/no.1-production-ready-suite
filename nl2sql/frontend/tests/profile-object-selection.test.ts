import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applySchemaBulkSelection,
  selectedObjectKeys,
  toggleObjectSelection,
} from "../src/features/nl2sql/profileObjectSelection.ts";

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

test("トグルは表記が違う保存値でも同じ object として外せる", () => {
  assert.deepEqual(toggleObjectSelection(['"APP"."TABLE_01"'], "APP.TABLE_01"), []);
  assert.deepEqual(toggleObjectSelection(["app.table_01"], "APP.TABLE_01"), []);
  assert.deepEqual(toggleObjectSelection(["APP.TABLE_02"], "APP.TABLE_01"), [
    "APP.TABLE_02",
    "APP.TABLE_01",
  ]);
});

test("トグルは重複を積み上げない", () => {
  let selection: string[] = [];
  for (let index = 0; index < 3; index += 1) {
    selection = toggleObjectSelection(selection, "APP.TABLE_01");
    selection = toggleObjectSelection(selection, '"APP"."TABLE_01"');
  }
  assert.deepEqual(selection, []);
  assert.deepEqual(toggleObjectSelection(selection, "app.table_01"), ["APP.TABLE_01"]);
});

test("突合用集合は引用符と大文字小文字を吸収する", () => {
  assert.deepEqual(
    [...selectedObjectKeys(['"APP"."TABLE_01"', "app.table_02"])].sort(),
    ["APP.TABLE_01", "APP.TABLE_02"],
  );
});

test("チェック判定・トグル・件数が同じ正規化キーを共有する", () => {
  assert.match(profilePage, /selectedObjectKeys\(selectedItems\)/u);
  assert.match(profilePage, /toggleObjectSelection\(current\[key\], name\)/u);
  assert.match(profilePage, /selected=\{selectedSet\.has\(normalizeObjectKey\(qualified\)\)\}/u);
  assert.match(profilePage, /const ownerKeyPrefix = normalizeObjectKey\(`\$\{owner\}\.`\);/u);
  assert.doesNotMatch(profilePage, /current\[key\]\.includes\(name\)/u);
});

test("手動更新の失敗は初回ロード用 Banner state と分離して保持する", () => {
  // クエリ状態を監視する effect は message を空文字で上書きするため、
  // 手動更新の失敗を同じ state に載せると通知が消える。
  assert.match(profilePage, /const \[refreshError, setRefreshError\] = useState\(""\);/u);
  assert.match(profilePage, /setRefreshError\(t\("profiles\.error\.load"\)\);/u);
  assert.doesNotMatch(profilePage, /setMessage\(t\("profiles\.error\.load"\)\);/u);
  assert.match(profilePage, /: refreshError\n\s*\? \{ tone: "danger" as const/u);
});

test("保存成功時に実行確認語をクリアして破壊的操作のゲートを再武装する", () => {
  assert.match(
    profilePage,
    /setRequiredErrors\(\{\}\);\n\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*setOracleConfirmation\(""\);/u,
  );
});
