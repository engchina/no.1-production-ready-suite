import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { WIDE_PAGE_ROUTES } from "./page-layout";
import { APP_ROUTES } from "./routes";

// 作業画面（多列の表、会話 + 比較、プレビュー + 抽出）は画面幅いっぱい、読む・入力する画面は 1440px。
// PageHeader と PageBody の wide がずれると、1920px 以上でタイトルと本文の左端がずれる。
const SRC_DIR = join(__dirname, "..");

/** 作業画面ごとに、PageHeader / PageBody を描く画面ファイル（App.tsx 内のルート関数は関数名で絞る）。 */
const WIDE_PAGE_SOURCES: Record<string, { file: string; fn?: string }> = {
  [APP_ROUTES.chat]: { file: "components/chat/ChatClient.tsx" },
  // 文書ワークスペースは PageHeader を持たず、戻るリンクと本文の 2 つの PageBody で構成する。
  [APP_ROUTES.documents]: { file: "App.tsx", fn: "DocumentDetailRoute" },
  [APP_ROUTES.fileList]: { file: "components/file-list/FileListClient.tsx" },
  [APP_ROUTES.feedback]: { file: "components/feedback/FeedbackClient.tsx" },
};

const LAYOUT_TAG = /<(PageHeader|PageBody)\b[^>]*>/gu;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.name.endsWith(".tsx") ? [relative(SRC_DIR, path)] : [];
  });
}

/** App.tsx のようにルート関数が並ぶファイルから、1 つの関数の本文だけを切り出す。 */
function functionSource(source: string, fn: string): string {
  const start = source.indexOf(`function ${fn}(`);
  expect(start, `${fn} が見つかる`).toBeGreaterThanOrEqual(0);
  const next = source.indexOf("\nfunction ", start + 1);
  return source.slice(start, next === -1 ? undefined : next);
}

function layoutTags(source: string): string[] {
  return [...source.matchAll(LAYOUT_TAG)].map((match) => match[0]);
}

const hasWide = (tag: string) => /\swide\b/u.test(tag);

describe("作業画面の wide 判定（WIDE_PAGE_ROUTES）", () => {
  it("一覧のすべての作業画面に検査対象の画面ファイルがある", () => {
    for (const route of WIDE_PAGE_ROUTES) {
      expect(WIDE_PAGE_SOURCES[route], `${route} の画面ファイル`).toBeDefined();
    }
    expect(Object.keys(WIDE_PAGE_SOURCES).sort()).toEqual([...WIDE_PAGE_ROUTES].sort());
  });

  it("作業画面は PageHeader / PageBody のすべてに wide を渡す", () => {
    for (const [route, { file, fn }] of Object.entries(WIDE_PAGE_SOURCES)) {
      const source = readFileSync(join(SRC_DIR, file), "utf8");
      const tags = layoutTags(fn ? functionSource(source, fn) : source);
      expect(tags.some((tag) => tag.startsWith("<PageBody")), `${route}（${file}）に PageBody がある`).toBe(true);
      for (const tag of tags) expect(hasWide(tag), `${route}（${file}）: ${tag}`).toBe(true);
    }
  });

  it("作業画面以外の PageHeader / PageBody は wide を渡さず 1440px のまま", () => {
    for (const file of sourceFiles(SRC_DIR)) {
      let source = readFileSync(join(SRC_DIR, file), "utf8");
      for (const wide of Object.values(WIDE_PAGE_SOURCES)) {
        if (wide.file !== file) continue;
        source = wide.fn ? source.replace(functionSource(source, wide.fn), "") : "";
      }
      for (const tag of layoutTags(source)) expect(hasWide(tag), `${file}: ${tag}`).toBe(false);
    }
  });
});
