import { expect, it } from "vitest";
import { normalizeGraphSearch } from "../src/lib/graph-search";

it("matches full-width Latin labels without changing word boundaries", () => {
  expect(normalizeGraphSearch("ＡＢＣ 表")).toBe("abc 表");
  expect(normalizeGraphSearch("AB C")).not.toBe(normalizeGraphSearch("ABC"));
});
