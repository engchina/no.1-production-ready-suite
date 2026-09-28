import { describe, expect, it } from "vitest";

import { docragUnusedNoteKey } from "./docrag-unused";

describe("docragUnusedNoteKey", () => {
  it("回答エンジンが標準なら注記しない", () => {
    expect(docragUnusedNoteKey("standard")).toBeNull();
  });

  it("DocRAG を明示したときは「DocRAG のため使われない」", () => {
    expect(docragUnusedNoteKey("docrag")).toBe("businessViews.docragUnused.docrag");
  });

  it.each([null, undefined])("継承(%s)のときは「DocRAG のときは使われない」", (value) => {
    expect(docragUnusedNoteKey(value)).toBe("businessViews.docragUnused.inherit");
  });
});
