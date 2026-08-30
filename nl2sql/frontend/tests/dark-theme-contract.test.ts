import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const globalsSource = readFileSync(new URL("../src/globals.css", import.meta.url), "utf8");
const statusBadgeSource = readFileSync(
  new URL("../src/components/ui/status-badge.tsx", import.meta.url),
  "utf8"
);

type Rgb = readonly [number, number, number];

function darkTokens(): Map<string, string> {
  const block = globalsSource.match(/\.dark\s*\{(?<body>[\s\S]*?)\n\}/u)?.groups?.body;
  assert.ok(block, "globals.css に .dark token block が必要です");
  return new Map(
    [...block.matchAll(/--(?<name>[a-z0-9-]+):\s*(?<value>#[0-9a-f]{6});/gu)].map(
      (match) => [match.groups!.name, match.groups!.value]
    )
  );
}

function rgb(hex: string): Rgb {
  return [0, 2, 4].map((offset) => Number.parseInt(hex.slice(offset + 1, offset + 3), 16)) as unknown as Rgb;
}

function relativeLuminance(hex: string): number {
  const linear = rgb(hex).map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
    (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  );
}

function blend(foreground: string, background: string, alpha: number): string {
  const foregroundRgb = rgb(foreground);
  const backgroundRgb = rgb(background);
  const channels = foregroundRgb.map((channel, index) =>
    Math.round(channel * alpha + backgroundRgb[index]! * (1 - alpha))
  );
  return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
}

function requireToken(tokens: Map<string, string>, name: string): string {
  const value = tokens.get(name);
  assert.ok(value, `dark token --${name} が必要です`);
  return value;
}

function assertContrast(
  tokens: Map<string, string>,
  foreground: string,
  background: string,
  minimum: number
) {
  const ratio = contrastRatio(
    requireToken(tokens, foreground),
    requireToken(tokens, background)
  );
  assert.ok(
    ratio >= minimum,
    `--${foreground} / --${background} は ${ratio.toFixed(2)}:1（必要 ${minimum}:1）`
  );
}

test("dark theme の文字と semantic feedback は WCAG AA を満たす", () => {
  const tokens = darkTokens();
  for (const surface of ["background", "card"]) {
    for (const foreground of ["foreground", "muted", "primary", "success", "warning", "danger", "info"]) {
      assertContrast(tokens, foreground, surface, 4.5);
    }
    const muted = requireToken(tokens, "muted");
    const surfaceColor = requireToken(tokens, surface);
    assert.ok(
      contrastRatio(blend(muted, surfaceColor, 0.7), surfaceColor) >= 4.5,
      `70% muted / --${surface} は 4.5:1 以上が必要です`
    );
  }

  for (const tone of ["success", "warning", "danger", "info"]) {
    assertContrast(tokens, tone, `${tone}-bg`, 4.5);
  }
  assertContrast(tokens, "primary-foreground", "primary", 4.5);
  assertContrast(tokens, "primary-fill-foreground", "primary-fill", 4.5);

  for (const tonalSurface of [
    "surface-muted-5",
    "surface-muted-15",
    "surface-muted-20",
    "surface-muted-30",
    "surface-muted-40",
  ]) {
    assertContrast(tokens, "foreground", tonalSurface, 4.5);
    assertContrast(tokens, "muted", tonalSurface, 4.5);
  }
  assert.match(globalsSource, /\.dark \.bg-muted\\\/30/u);
});

test("dark theme の control・solid action・navigation は 3:1 の境界を持つ", () => {
  const tokens = darkTokens();
  for (const surface of ["background", "card"]) {
    assertContrast(tokens, "control-border", surface, 3);
  }
  for (const fill of ["primary-fill", "success-fill", "danger-fill"]) {
    assertContrast(tokens, fill, "card", 3);
  }
  assertContrast(tokens, "sidebar-active", "sidebar", 3);
  assertContrast(tokens, "primary-fill-foreground", "sidebar-active", 4.5);
});

test("ontology graph はノード文字と既定線の contrast を保つ", () => {
  const tokens = darkTokens();
  for (const nodeFill of [
    "graph-entity",
    "graph-metric",
    "graph-sql",
    "graph-term",
    "graph-default",
  ]) {
    assertContrast(tokens, "graph-fg", nodeFill, 4.5);
  }
  assertContrast(tokens, "graph-line", "background", 3);
});

test("StatusBadge は固定 light palette ではなく semantic token を使う", () => {
  for (const variant of ["neutral", "info", "pending", "success", "warning", "danger"]) {
    assert.match(statusBadgeSource, new RegExp(`^\\s*${variant}:`, "mu"));
  }
  assert.doesNotMatch(
    statusBadgeSource,
    /(?:bg|text)-(?:slate|sky|amber|emerald|yellow|red)-\d+/u
  );
  assert.match(statusBadgeSource, /data-status-variant=\{variant\}/u);
});
