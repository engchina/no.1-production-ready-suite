/**
 * 3 製品の e2e が共通に使う、自前ホストの書体の検査（#3292）。
 *
 * - `FONT_SANS_PREFIX` / `FONT_MONO_FAMILY`: 共有 tokens の `--font-sans` / `--font-mono` の計算値（先頭・全体）
 * - `CJK_FALLBACK_SAMPLES`: Noto Sans JP に無く、Noto Sans SC・TC・HK のそれぞれだけにある合成の字
 * - `loadCjkFallbackFonts`: Noto Sans SC / TC / HK の各太さを、合成の字で `document.fonts.load` する
 * - `renderedCjkFallbackFonts`: 合成の字を本文の書体（`--font-sans`）と等幅（`code`）で描き、Chromium が実際に使った
 *   書体の名前を CDP（`CSS.getPlatformFontsForNode`）で返す。JP が先頭のまま、JP に無い字だけが SC → TC → HK で補われるか
 *
 * 製品の spec は相対パスで import し（RAG・Agent `../../../platform/packages/ui/e2e/font-checks`、NL2SQL
 * `../../../../platform/packages/ui/e2e/font-checks`）、結果を製品の `expect` で確かめる。
 * `@playwright/test` の型は import しない（`layout-checks.ts` と同じ理由）。
 */

/** 本文の書体（`--font-sans`）の計算値の先頭。日本語の字形を優先し、JP に無い字だけを SC → TC → HK で補う。 */
export const FONT_SANS_PREFIX = '"Noto Sans JP", "Noto Sans SC", "Noto Sans TC", "Noto Sans HK", Roboto,';

/** 等幅書体（`--font-mono`）の計算値。Google Sans Code に無い字は本文と同じ順で補う。 */
export const FONT_MONO_FAMILY = '"Google Sans Code", "Noto Sans JP", "Noto Sans SC", "Noto Sans TC", "Noto Sans HK", Roboto, monospace';

/**
 * Noto Sans JP に無い合成の字（業務の内容ではなく、字の収録の違いだけで選ぶ）。
 * 「们」は SC だけ、「亷」は TC と HK（TC が先）、「乪」は HK だけに収録される（JP には無い）。
 */
export const CJK_FALLBACK_SAMPLES = [
  { id: "simplified", family: "Noto Sans SC", text: "们" },
  { id: "traditional", family: "Noto Sans TC", text: "亷" },
  { id: "hongkong", family: "Noto Sans HK", text: "乪" },
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

/** Noto Sans SC / TC / HK の各太さを、それぞれの合成の字で読み込む。unicode-range の分割のうち、その字の分だけを取る。 */
export async function loadCjkFallbackFonts(page: EvaluatePage, weights: readonly number[]): Promise<LoadedFont[]> {
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
