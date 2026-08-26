import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/components/ui/select-field.tsx", import.meta.url), "utf8");

test("SelectField は選択後に主スクロールを動かさず trigger focus を戻す", () => {
  assert.match(source, /buttonRef\.current\?\.focus\(\{ preventScroll: true \}\)/u);
});
