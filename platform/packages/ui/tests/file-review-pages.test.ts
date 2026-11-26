import { describe, expect, it } from "vitest";

import { files, passagePageLabel, type ReviewCitation } from "../src/components/feedback/file-review-model";

function hit(index: number, start?: number, end?: number): ReviewCitation {
  return { document_id: "doc-a", chunk_id: String(index), locator: { page_start: start, page_end: end } };
}

describe("資料カードの命中ページ", () => {
  it("順不同の連続する 80 ページと重複を 1 つの範囲にまとめる", () => {
    const hits = Array.from({ length: 80 }, (_, i) => hit(i, 80 - i));
    expect(files([...hits, hits[0]])[0].pages).toEqual({ label: "p.1–80", remaining: 0 });
  });

  it("重なる範囲をまとめ、間が空いたページは区別し、省略した実際のページ数を返す", () => {
    const hits = [hit(1, 2, 5), hit(2, 1, 3), hit(3, 8), hit(4, 11), hit(5, 14, 16), hit(6, 19)];
    expect(files(hits)[0].pages).toEqual({ label: "p.1–5、8、11", remaining: 4 });
    expect(passagePageLabel(hits[0])).toBe("p.2–5");
  });

  it("ページのない資料に番号を補わず、不正な終了ページは開始ページだけにする", () => {
    expect(files([hit(1), hit(2, -1)])[0].pages).toEqual({ label: "", remaining: 0 });
    expect(passagePageLabel(hit(3, 5, 2))).toBe("p.5");
  });
});
