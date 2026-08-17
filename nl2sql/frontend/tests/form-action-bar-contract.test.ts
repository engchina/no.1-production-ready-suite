import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/components/FormActionBar.tsx", import.meta.url),
  "utf8"
);

test("FormActionBar は primary / secondary / danger を descriptor で分離する", () => {
  assert.match(source, /export interface FormActionDescriptor/u);
  assert.match(source, /export interface FormActionBarProps/u);
  assert.match(source, /primaryActions/u);
  assert.match(source, /secondaryActions/u);
  assert.match(source, /dangerActions/u);
  assert.ok(source.indexOf("primaryActions.map") < source.indexOf("secondaryActions.map"));
});

test("FormActionBar は danger を通常の赤ボタンとして直置きしない", () => {
  assert.doesNotMatch(source, /variant="danger"/u);
  assert.match(source, /data-form-action-tone="danger"/u);
  assert.match(source, /text-danger/u);
  assert.match(source, /border-t border-border pt-1/u);
  assert.match(source, /t\("common\.actions\.more"\)/u);
});

test("FormActionBar の danger menu は ARIA とキーボード契約を持つ", () => {
  assert.match(source, /aria-haspopup="menu"/u);
  assert.match(source, /aria-expanded=\{open\}/u);
  assert.match(source, /aria-controls=\{menuId\}/u);
  assert.match(source, /role="menu"/u);
  assert.match(source, /role="menuitem"/u);
  for (const key of ["Escape", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.match(source, new RegExp(`event\\.key === "${key}"`, "u"));
  }
  assert.match(source, /triggerRef\.current\?\.focus/u);
});

test("FormActionBar は mobile で全幅 44px、desktop で通常 action bar 高さに戻す", () => {
  assert.match(source, /h-\[44px\] w-full whitespace-nowrap sm:h-10 sm:w-auto/u);
  assert.match(source, /flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center/u);
});
