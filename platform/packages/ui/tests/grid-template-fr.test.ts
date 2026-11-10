import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * grid-cols の任意の値で minmax(0,…) に包まない fr を止める adherence の規則（#4803）。製品は lint が検出する。
 * platform の 2 パッケージは lint を掛けていないため、adherence の同じ正規表現をここで掛ける。
 */
const packagesDir = fileURLToPath(new URL("../../", import.meta.url));
const adherencePath = fileURLToPath(new URL("../../../docs/design-system/adherence.oxlintrc.json", import.meta.url));
const SOURCE_DIRS = ["ui/src", "system-settings/src"].map((dir) => join(packagesDir, dir));

function adherenceGridRule(): RegExp {
  const config = JSON.parse(readFileSync(adherencePath, "utf8")) as {
    overrides: { rules: Record<string, [string, ...{ selector: string }[]]> }[];
  };
  const [, ...rules] = config.overrides[0].rules["design-system/restricted-syntax"];
  const selector = rules.find((rule) => rule.selector.includes("grid-cols-"))?.selector ?? "";
  const source = selector.match(/Literal\[value=\/(.+?)\/\], TemplateElement/)?.[1];
  if (!source) throw new Error("adherence に grid-cols の規則が無い");
  return new RegExp(source);
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sourceFiles(path, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

describe("grid-cols の任意の値の fr（#4803）", () => {
  const rule = adherenceGridRule();

  it("minmax(0,…) に包まない fr を検出し、minmax(固定の最小,1fr) と包んだ fr は通す", () => {
    for (const bad of ["sm:grid-cols-[max-content_1fr]", "md:grid-cols-[1fr_auto]", "sm:grid-cols-[auto_1fr]", "grid-cols-[repeat(3,1fr)]", "grid-cols-[1.5fr]"]) {
      expect(rule.test(bad), bad).toBe(true);
    }
    for (const good of [
      "grid-cols-[minmax(0,1fr)_auto]",
      "grid-cols-[minmax(8rem,1fr)_auto]",
      "xl:grid-cols-[repeat(3,minmax(0,1fr))_auto]",
      "grid-cols-[repeat(auto-fill,minmax(min(100%,16rem),1fr))]",
      "grid-cols-[7rem_minmax(0,1fr)]",
      "grid-rows-[auto_1fr]",
      "grid-cols-2",
    ]) {
      expect(rule.test(good), good).toBe(false);
    }
  });

  it("platform の ui と system-settings に違反が無い", () => {
    const findings: string[] = [];
    for (const dir of SOURCE_DIRS) {
      for (const file of sourceFiles(dir)) {
        readFileSync(file, "utf8").split("\n").forEach((line, index) => {
          if (rule.test(line)) findings.push(`${file.slice(packagesDir.length)}:${index + 1}`);
        });
      }
    }
    expect(findings).toEqual([]);
  });
});
