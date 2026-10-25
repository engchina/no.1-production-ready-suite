import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { collectFontLicenses, fontLicenses, fontLicensesIndex } from "../vite/font-licenses.mjs";

// 書体のライセンスの原文を build の成果物に同梱する plugin（#3392）。合成の package で確かめる。
const roots: string[] = [];

function makeProduct(packages: Record<string, { license?: string; licenseFile?: string | null }>, extra = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "font-licenses-"));
  roots.push(root);
  const names = Object.keys(packages);
  writeFileSync(
    path.join(root, "package.json"),
    JSON.stringify({
      name: "product",
      dependencies: Object.fromEntries(names.filter((n) => !n.includes("dev-only")).map((n) => [n, "1.0.0"])),
      devDependencies: { ...Object.fromEntries(names.filter((n) => n.includes("dev-only")).map((n) => [n, "1.0.0"])), ...extra },
    }),
  );
  for (const [name, spec] of Object.entries(packages)) {
    const dir = path.join(root, "node_modules", ...name.split("/"));
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, "package.json"), JSON.stringify({ name, version: "5.0.0", license: spec.license ?? "OFL-1.1" }));
    if (spec.licenseFile !== null) writeFileSync(path.join(dir, spec.licenseFile ?? "LICENSE"), `license of ${name}\n`);
  }
  return root;
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("collectFontLicenses", () => {
  it("dependencies と devDependencies の @fontsource* の原文を集め、書体とライセンスの名前のファイルにする", () => {
    const root = makeProduct(
      {
        "@fontsource-variable/font-a": {},
        "@fontsource/dev-only-font-b": { licenseFile: "OFL.txt" },
        "@fontsource/font-c": { license: "Apache-2.0" },
      },
      { "not-a-font": "1.0.0" },
    );
    const items = collectFontLicenses(root);
    expect(items.map((item) => [item.name, item.fileName, item.text])).toEqual([
      ["@fontsource-variable/font-a", "fonts/licenses/font-a-OFL.txt", "license of @fontsource-variable/font-a\n"],
      ["@fontsource/dev-only-font-b", "fonts/licenses/dev-only-font-b-OFL.txt", "license of @fontsource/dev-only-font-b\n"],
      ["@fontsource/font-c", "fonts/licenses/font-c-Apache.txt", "license of @fontsource/font-c\n"],
    ]);
    expect(fontLicensesIndex(items)).toContain("@fontsource-variable/font-a@5.0.0  OFL-1.1  font-a-OFL.txt");
  });

  it("原文が無い書体は build を止める", () => {
    const root = makeProduct({ "@fontsource/font-a": { licenseFile: null } });
    expect(() => collectFontLicenses(root)).toThrow(/ライセンスの原文/);
  });

  it("node_modules に無い書体は build を止める", () => {
    const root = makeProduct({});
    writeFileSync(path.join(root, "package.json"), JSON.stringify({ dependencies: { "@fontsource/missing": "1.0.0" } }));
    expect(() => collectFontLicenses(root)).toThrow(/node_modules にありません/);
  });
});

describe("fontLicenses（Vite の plugin）", () => {
  it("build のときだけ動き、原文と一覧を asset として出す", () => {
    const root = makeProduct({ "@fontsource/font-a": {} });
    const plugin = fontLicenses();
    expect(plugin.apply).toBe("build");
    plugin.configResolved({ root });
    const emitted: Array<{ fileName: string; source: string }> = [];
    plugin.generateBundle.call({
      emitFile: (file) => {
        emitted.push({ fileName: file.fileName, source: file.source });
        return file.fileName;
      },
    });
    expect(emitted.map((file) => file.fileName)).toEqual(["fonts/licenses/font-a-OFL.txt", "fonts/licenses/README.txt"]);
  });
});
