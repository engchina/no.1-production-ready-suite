import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(new URL(`../src/styles/${path}`, import.meta.url), "utf8");
const colors = read("tokens/colors.css");

describe("tokens CSS", () => {
  it("旧トークン名の互換層（compat.css / 旧名ユーティリティ）を持たない", () => {
    const entry = read("tokens.css");
    expect(entry).not.toMatch(/compat\.css/);
    expect(entry).not.toMatch(/--color-(card|muted|primary|background|foreground):/);
    expect(() => read("tokens/compat.css")).toThrow();
  });

  it("light-dark() には色だけを渡す（混合率などを渡すと宣言ごと無効になる）", () => {
    expect(colors).not.toMatch(/light-dark\(\s*\d+%/);
  });

  it("意味トークンを color-scheme を切り替える要素すべてで宣言し直す（Lightning CSS 変換後もスコープ内でダーク値になる）", () => {
    expect(colors).toMatch(/:root, \[data-theme\], \[data-surface\] \{\s*--font-sans/);
  });

  it("テーマ class（.light / .dark）はルート要素でだけ効く（外部ライブラリの同名 class に反応しない）", () => {
    for (const css of [colors, read("tokens/a11y.css")]) {
      expect(css.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/(^|[\s,])\.(light|dark)\b/m);
    }
    expect(colors).toMatch(/:root\.dark \{ color-scheme: dark; \}/);
  });

  it("文字は px、ルートは 14px のまま（余白の rem を動かさない）", () => {
    expect(read("tokens/base.css")).toMatch(/html\s*\{\s*font-size:\s*14px;/);
    expect(read("tokens/typography.css")).toMatch(/--font-size-sm:\s*14px;/);
    expect(read("tokens/typography.css")).toMatch(/--font-size-xs:\s*12px;/);
    expect(read("tokens.css")).toMatch(/--text-sm:\s*var\(--font-size-sm\);/);
    expect(read("tokens.css")).toMatch(/--text-xs:\s*var\(--font-size-xs\);/);
  });

  it("サイドバー幅はトークンが正本で、日本語のナビ項目名が収まる 18rem（252px）", () => {
    expect(read("tokens/spacing.css")).toMatch(/--sidebar-width:\s*18rem;/);
    const sidebar = readFileSync(new URL("../src/components/app-shell/Sidebar.tsx", import.meta.url), "utf8");
    expect(sidebar).toContain('"w-[var(--sidebar-width-collapsed)]" : "w-[var(--sidebar-width)]"');
    expect(sidebar).not.toMatch(/\bw-60\b/);
  });

  it("通知はモーダルの下に重なる（toast < scrim < dialog < palette）", () => {
    const elevation = read("tokens/elevation.css");
    const z = (name: string) => Number(elevation.match(new RegExp(`--z-${name}:\\s*(\\d+);`))?.[1]);
    expect(z("sticky")).toBeLessThan(z("toast"));
    expect(z("toast")).toBeLessThan(z("scrim"));
    expect(z("scrim")).toBeLessThan(z("dialog"));
    expect(z("dialog")).toBeLessThan(z("palette"));
    const toast = readFileSync(new URL("../src/components/ui/toast.tsx", import.meta.url), "utf8");
    expect(toast).toContain("z-[var(--z-toast)]");
  });

  it("タッチ端末ではボタン高さを 44px にする", () => {
    expect(read("tokens/spacing.css")).toMatch(
      /@media \(pointer: coarse\) \{\s*:root \{\s*--button-height-sm: var\(--control-height-touch\);\s*--button-height-md: var\(--control-height-touch\);\s*--button-height-lg: var\(--control-height-touch\);/
    );
  });

  it("フォーム入力のフォーカスリングは外側へはみ出さない（内側 1px のリング）", () => {
    const base = read("tokens/base.css");
    const rule = base.match(
      /:is\(input, textarea, select\):not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):focus-visible\s*\{([^}]*)\}/
    )?.[1];
    expect(rule).toMatch(/outline:\s*none;/);
    expect(rule).toMatch(/box-shadow:\s*inset 0 0 0 1px var\(--color-focus-ring\);/);
  });

  it("淡アクセント面（[data-surface-tint=\"accent\"]）の副次・アクセント文字は両テーマで 4.5:1 以上", () => {
    const palette = read("tokens/palette.css");
    const rgb = (name: string) => {
      const hex = palette.match(new RegExp(`--${name}:\\s*#([0-9a-f]{6});`, "i"))?.[1];
      if (!hex) throw new Error(`--${name} が palette.css に無い`);
      return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    };
    const luminance = (c: number[]) => {
      const [r, g, b] = c.map((v) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const contrast = (a: number[], b: number[]) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    const mix = (a: number[], ratio: number, b: number[]) => a.map((v, i) => v * ratio + b[i] * (1 - ratio));

    const scope = colors.match(/\[data-surface-tint="accent"\] \{([^}]*)\}/)?.[1] ?? "";
    expect(scope).toMatch(/--color-fg-muted:\s*light-dark\(var\(--neutral-650\), var\(--neutral-350\)\);/);
    expect(scope).toMatch(/--color-accent-fg:\s*var\(--color-accent-fg-strong\);/);
    expect(colors).toMatch(/--color-accent-fg-strong:\s*light-dark\(var\(--blue-650\), var\(--blue-300\)\);/);
    // --color-accent-subtle = emphasis（ライト blue-500 / ダーク blue-600）を surface（neutral-0 / neutral-900）に 8% / 16%
    const light = mix(rgb("blue-500"), 0.08, rgb("neutral-0"));
    const dark = mix(rgb("blue-600"), 0.16, rgb("neutral-900"));
    for (const [fg, bg] of [["neutral-650", light], ["blue-650", light], ["neutral-350", dark], ["blue-300", dark]] as const) {
      expect(contrast(rgb(fg), bg)).toBeGreaterThanOrEqual(4.5);
    }
    // 変更前の値（fg-muted = neutral-600、accent-fg = blue-500）はライトで不足していた
    expect(contrast(rgb("neutral-600"), light)).toBeLessThan(4.5);

    // [data-surface] と違い、面のトークン一式を宣言し直さない。a11y の応答層は対象に含める
    expect(colors).not.toMatch(/\[data-surface\], \[data-surface-tint\]/);
    const a11y = read("tokens/a11y.css");
    expect(a11y.match(/:root, \[data-theme\], \[data-surface\], \[data-surface-tint\] \{/g)).toHaveLength(2);
    expect(a11y).toMatch(/:root, \[data-theme\], \[data-surface-tint\] \{\s*--color-fg-muted:/);
  });

  it(".dark に色値の手書き宣言を持たない（テーマは light-dark() で解決する）", () => {
    for (const file of ["tokens.css", "tokens/colors.css", "tokens/base.css"]) {
      expect(read(file)).not.toMatch(/\.dark\s*\{[^}]*#[0-9a-f]{3,8}/i);
    }
  });
});
