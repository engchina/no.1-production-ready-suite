/**
 * 3 製品の e2e が共通に使う、自前ホストの書体の検査（#3292）。
 *
 * - `FONT_SANS_PREFIX` / `FONT_MONO_FAMILY`: 共有 tokens の `--font-sans` / `--font-mono` の計算値（先頭・全体）
 * - `CJK_FALLBACK_SAMPLES`: Noto Sans JP に無く、Noto Sans SC・TC・HK のそれぞれだけにある合成の字。SC・TC・HK は
 *   可変書体（`@fontsource-variable`）で、CSS の書体名（`family`）は `Noto Sans SC Variable` など、Chromium が描画に
 *   使った書体の名前（`renderedFamily`）は `Noto Sans SC` で始まる（#3328）
 * - `loadCjkFallbackFonts`: Noto Sans SC / TC / HK の各太さを、合成の字で `document.fonts.load` する
 * - `renderedCjkFallbackFonts`: 合成の字を本文の書体（`--font-sans`）と等幅（`code`）で描き、Chromium が実際に使った
 *   書体の名前を CDP（`CSS.getPlatformFontsForNode`）で返す。JP が先頭のまま、JP に無い字だけが SC → TC → HK で補われるか
 * - SC / TC / HK の `@font-face` は主の CSS に入れず、最初の描画の後に別の CSS の chunk で読む（#3305。
 *   `loadStylesAfterFirstRender`）。`recordFirstRenderFontFaces`（`page.goto` の前）が最初の描画の時点の `@font-face` の
 *   書体を記録し、`firstRenderFontFaces` で読む。`initialStylesheetFontFaces` は index.html が読む（preload を含む）CSS の
 *   `@font-face` の書体、`waitForCjkFallbackFaces` は後から読む face が登録されるまで待つ
 *
 * 製品の spec は相対パスで import し（RAG・Agent `../../../platform/packages/ui/e2e/font-checks`、NL2SQL
 * `../../../../platform/packages/ui/e2e/font-checks`）、結果を製品の `expect` で確かめる。
 * `@playwright/test` の型は import しない（`layout-checks.ts` と同じ理由）。
 */

/** 本文の書体（`--font-sans`）の計算値の先頭。日本語の字形を優先し、JP に無い字だけを SC → TC → HK で補う。 */
export const FONT_SANS_PREFIX = '"Noto Sans JP", "Noto Sans SC Variable", "Noto Sans TC Variable", "Noto Sans HK Variable", Roboto,';

/** 等幅書体（`--font-mono`）の計算値。Google Sans Code に無い字は本文と同じ順で補う。 */
export const FONT_MONO_FAMILY =
  '"Google Sans Code", "Noto Sans JP", "Noto Sans SC Variable", "Noto Sans TC Variable", "Noto Sans HK Variable", Roboto, monospace';

/**
 * Noto Sans JP に無い合成の字（業務の内容ではなく、字の収録の違いだけで選ぶ）。
 * 「们」は SC だけ、「亷」は TC と HK（TC が先）、「乪」は HK だけに収録される（JP には無い）。
 */
export const CJK_FALLBACK_SAMPLES = [
  { id: "simplified", family: "Noto Sans SC Variable", renderedFamily: "Noto Sans SC", text: "们" },
  { id: "traditional", family: "Noto Sans TC Variable", renderedFamily: "Noto Sans TC", text: "亷" },
  { id: "hongkong", family: "Noto Sans HK Variable", renderedFamily: "Noto Sans HK", text: "乪" },
] as const;

export type CjkFallbackSampleId = (typeof CJK_FALLBACK_SAMPLES)[number]["id"];

/** `page.evaluate` を持つもの（Playwright の `Page`）。 */
export interface EvaluatePage {
  evaluate<R, A>(pageFunction: (arg: A) => R | Promise<R>, arg: A): Promise<R>;
}

/** CDP の session（Playwright の `CDPSession`。`page.context().newCDPSession(page)`）。 */
export interface CdpSessionLike {
  send(method: never, params?: never): Promise<unknown>;
}

/** 書体の 1 つの太さを読み込んだ結果。`count` は読み込めた face の数（0 なら自前ホストの face が無い）。 */
export interface LoadedFont {
  family: string;
  weight: number;
  count: number;
}

/** 最初の描画の後に読む書体（主の CSS に入れない。#3305）。 */
export const DEFERRED_CJK_FAMILIES: readonly string[] = CJK_FALLBACK_SAMPLES.map((sample) => sample.family);

/** `page.addInitScript` を持つもの（Playwright の `Page`）。 */
export interface InitScriptPage {
  addInitScript(script: (key: string) => void, key: string): Promise<unknown>;
}

/** 最初の描画（`#root` に最初の子が入った時点）の CSS の `@font-face` の書体（重複なし）。 */
export interface FirstRenderFontFaces {
  families: string[];
}

const FIRST_RENDER_KEY = "__prFirstRenderFontFaces";

/**
 * 最初の描画の時点の `@font-face` の書体を記録する init script を足す（`page.goto` の前に呼ぶ）。
 * `#root` に最初の子が入った commit の通知（microtask）で、その時点の全 stylesheet（dev の `<style>` を含む）を読む。
 */
export async function recordFirstRenderFontFaces(page: InitScriptPage): Promise<void> {
  await page.addInitScript((key: string) => {
    const families = () => {
      const found = new Set<string>();
      const walk = (rules: CSSRuleList) => {
        for (const rule of Array.from(rules)) {
          if (rule instanceof CSSFontFaceRule) {
            found.add(rule.style.getPropertyValue("font-family").replaceAll(/["']/gu, "").trim());
          } else if ("cssRules" in rule && rule.cssRules instanceof CSSRuleList) {
            walk(rule.cssRules);
          }
        }
      };
      for (const sheet of Array.from(document.styleSheets)) {
        try {
          walk(sheet.cssRules);
        } catch {
          // 別 origin の stylesheet は読めない（自前ホストの書体の CSS は同じ origin）。
        }
      }
      return [...found].sort();
    };
    const observer = new MutationObserver(() => {
      if (!document.getElementById("root")?.firstChild) return;
      observer.disconnect();
      (window as unknown as Record<string, unknown>)[key] = { families: families() };
    });
    observer.observe(document, { childList: true, subtree: true });
  }, FIRST_RENDER_KEY);
}

/** `recordFirstRenderFontFaces` が記録した最初の描画の時点の書体。描画されるまで待つ。 */
export async function firstRenderFontFaces(page: EvaluatePage, timeoutMs = 10_000): Promise<FirstRenderFontFaces> {
  return page.evaluate(
    async ({ key, timeoutMs }) => {
      const deadline = Date.now() + timeoutMs;
      for (;;) {
        const recorded = (window as unknown as Record<string, unknown>)[key] as FirstRenderFontFaces | undefined;
        if (recorded) return recorded;
        if (Date.now() > deadline) throw new Error("最初の描画の記録がありません（recordFirstRenderFontFaces を goto の前に呼ぶ）");
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    },
    { key: FIRST_RENDER_KEY, timeoutMs }
  );
}

/**
 * index.html が読む CSS（`<link>` の href が `.css` のもの。stylesheet・preload を含む）の `@font-face` の書体。
 * build の成果物（preview・本番）で、後から読む chunk が主の CSS・preload に入っていないことを確かめる
 * （dev サーバの index.html は CSS の `<link>` を持たないので `hrefs` は空になる）。
 */
export async function initialStylesheetFontFaces(page: EvaluatePage): Promise<{ hrefs: string[]; families: string[] }> {
  return page.evaluate(async () => {
    const html = await (await fetch(location.href, { cache: "no-store" })).text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const hrefs = Array.from(doc.querySelectorAll("link[href]"))
      .map((link) => new URL(link.getAttribute("href") ?? "", location.href))
      .filter((url) => url.pathname.endsWith(".css"))
      .map((url) => url.href);
    const families = new Set<string>();
    for (const href of hrefs) {
      const css = await (await fetch(href)).text();
      for (const match of css.matchAll(/@font-face\s*\{[^}]*?font-family:\s*([^;}]+)/gu)) {
        families.add((match[1] ?? "").replaceAll(/["']/gu, "").trim());
      }
    }
    return { hrefs, families: [...families].sort() };
  }, undefined);
}

/** 最初の描画の後に読む Noto Sans SC / TC / HK の face が `document.fonts` に登録されるまで待つ。 */
export async function waitForCjkFallbackFaces(page: EvaluatePage, timeoutMs = 10_000): Promise<void> {
  await page.evaluate(
    async ({ families, timeoutMs }) => {
      const deadline = Date.now() + timeoutMs;
      const registered = () => {
        const names = new Set(Array.from(document.fonts, (face) => face.family.replaceAll(/["']/gu, "")));
        return families.every((family) => names.has(family));
      };
      while (!registered()) {
        if (Date.now() > deadline) throw new Error(`後から読む書体が登録されません: ${families.join(", ")}`);
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    },
    { families: [...DEFERRED_CJK_FAMILIES], timeoutMs }
  );
}

/**
 * Noto Sans SC / TC / HK の各太さを、それぞれの合成の字で読み込む。unicode-range の分割のうち、その字の分だけを取る。
 * face は最初の描画の後に登録されるので、先に `waitForCjkFallbackFaces` で待つ。
 */
export async function loadCjkFallbackFonts(page: EvaluatePage, weights: readonly number[]): Promise<LoadedFont[]> {
  await waitForCjkFallbackFaces(page);
  return page.evaluate(
    async ({ samples, weights }) => {
      const loaded: { family: string; weight: number; count: number }[] = [];
      for (const sample of samples) {
        for (const weight of weights) {
          const faces = await document.fonts.load(`${weight} 14px "${sample.family}"`, sample.text);
          loaded.push({
            family: sample.family,
            weight,
            count: faces.filter((face) => face.status === "loaded").length,
          });
        }
      }
      return loaded;
    },
    { samples: CJK_FALLBACK_SAMPLES.map((sample) => ({ ...sample })), weights: [...weights] }
  );
}

/** 合成の字を描いたときに Chromium が使った書体の名前（`familyName`。太さの名前が付くことがある）。 */
export type RenderedCjkFallbackFonts = Record<`${CjkFallbackSampleId}-${"sans" | "mono"}`, string[]>;

/**
 * 合成の字を本文の書体（`body` を継承する `span`）と等幅（`code`）で描き、実際に使った書体の名前を返す。
 * 描いた要素は返す前に消す。
 */
export async function renderedCjkFallbackFonts(
  page: EvaluatePage,
  cdp: CdpSessionLike
): Promise<RenderedCjkFallbackFonts> {
  await waitForCjkFallbackFaces(page);
  await page.evaluate(async (samples) => {
    const host = document.createElement("div");
    host.setAttribute("data-cjk-font-samples", "");
    for (const sample of samples) {
      const sans = document.createElement("span");
      sans.setAttribute("data-cjk-font-sample", `${sample.id}-sans`);
      sans.textContent = sample.text;
      const mono = document.createElement("code");
      mono.setAttribute("data-cjk-font-sample", `${sample.id}-mono`);
      mono.textContent = sample.text;
      host.append(sans, mono);
    }
    document.body.append(host);
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, CJK_FALLBACK_SAMPLES.map((sample) => ({ ...sample })));

  // Playwright の CDPSession.send は CDP の method 名で型が決まる。ここで使う 4 つだけを型にする。
  const send = cdp.send.bind(cdp) as unknown as (
    method: string,
    params?: Record<string, unknown>
  ) => Promise<{ root?: { nodeId: number }; nodeId?: number; fonts?: { familyName: string }[] }>;
  const result = {} as RenderedCjkFallbackFonts;
  try {
    await send("DOM.enable");
    await send("CSS.enable");
    const { root } = await send("DOM.getDocument");
    for (const sample of CJK_FALLBACK_SAMPLES) {
      for (const kind of ["sans", "mono"] as const) {
        const key = `${sample.id}-${kind}` as const;
        const { nodeId } = await send("DOM.querySelector", {
          nodeId: root?.nodeId,
          selector: `[data-cjk-font-sample="${key}"]`,
        });
        const { fonts } = await send("CSS.getPlatformFontsForNode", { nodeId });
        result[key] = (fonts ?? []).map((font) => font.familyName);
      }
    }
  } finally {
    await page.evaluate(() => document.querySelector("[data-cjk-font-samples]")?.remove(), undefined);
  }
  return result;
}
