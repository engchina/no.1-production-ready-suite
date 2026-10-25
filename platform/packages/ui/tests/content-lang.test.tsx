// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AnswerPlainText,
  AnswerSections,
  ChatTurn,
  ChatUserMessage,
  CONTENT_LANG_SCAN_LIMIT,
  detectContentLang,
  DocumentPreview,
  EvidenceCard,
  ExcerptContent,
  MarkdownText,
  normalizeContentLang,
  resolveContentLang,
  type EvidenceListItem,
} from "../src";
import { SIMPLIFIED_ONLY_HAN, TRADITIONAL_ONLY_HAN } from "../src/lib/han-script-sets";

// 合成の例文（業務の内容ではない）。字形の違いを確かめるための一般的な文だけを使う。
const JA = "これはテスト用の例文で、字形の違いを確かめます。";
const ZH_HANS = "这是一个用于测试的示例句子，我们说明字形的区别。";
const ZH_HANT = "這是一個用於測試的範例句子，我們說明字形的區別。";

describe("detectContentLang", () => {
  it("かなが 2 割以上なら日本語", () => {
    expect(detectContentLang(JA)).toBe("ja");
    expect(detectContentLang("テスト")).toBe("ja");
  });

  it("簡体字だけ・繁体字だけの字で簡体と繁体を分ける", () => {
    expect(detectContentLang(ZH_HANS)).toBe("zh-Hans");
    expect(detectContentLang(ZH_HANT)).toBe("zh-Hant");
    expect(detectContentLang("这个怎么用？")).toBe("zh-Hans");
  });

  it("中国語の文に混ざる外来語のカタカナは日本語にしない", () => {
    expect(detectContentLang("我们用テスト这个工具进行测试和说明它们的区别")).toBe("zh-Hans");
  });

  it("かながある日本語の文の旧字 1 字は中国語にしない", () => {
    const traditional = TRADITIONAL_ONLY_HAN[0];
    expect(detectContentLang(`東京都内の${traditional}検査結果報告書類一覧表確認作業`)).toBe("ja");
  });

  it("漢字だけで印の字が無い・漢字が無い・簡体と繁体が拮抗するなら決めない", () => {
    expect(detectContentLang("東京都港区")).toBeUndefined();
    expect(detectContentLang("Hello, world 123")).toBeUndefined();
    expect(detectContentLang("")).toBeUndefined();
    expect(detectContentLang(null)).toBeUndefined();
    expect(detectContentLang(`${SIMPLIFIED_ONLY_HAN[0]}${TRADITIONAL_ONLY_HAN[0]}`)).toBeUndefined();
  });

  it("簡体字だけ・繁体字だけの字の集合は日本語の字（常用漢字の例）と重ならない", () => {
    const japanese = "国学会来東為対個骨直写説読";
    for (const char of japanese) {
      expect(SIMPLIFIED_ONLY_HAN.includes(char), char).toBe(false);
      expect(TRADITIONAL_ONLY_HAN.includes(char), char).toBe(false);
    }
    for (const char of SIMPLIFIED_ONLY_HAN) expect(TRADITIONAL_ONLY_HAN.includes(char), char).toBe(false);
  });

  it(`先頭の ${CONTENT_LANG_SCAN_LIMIT} 字だけを読む`, () => {
    expect(detectContentLang(`${"a".repeat(CONTENT_LANG_SCAN_LIMIT)}${ZH_HANS}`)).toBeUndefined();
  });
});

describe("normalizeContentLang / resolveContentLang", () => {
  it("取込の言語の値を :lang() の token の形にそろえる", () => {
    expect(normalizeContentLang("zh-CN")).toBe("zh-Hans");
    expect(normalizeContentLang("zh_SG")).toBe("zh-Hans");
    expect(normalizeContentLang("zh-TW")).toBe("zh-Hant");
    expect(normalizeContentLang("zh-Hant-HK")).toBe("zh-HK");
    expect(normalizeContentLang("zh-HK")).toBe("zh-HK");
    expect(normalizeContentLang("zh-hans")).toBe("zh-Hans");
    expect(normalizeContentLang("ja-JP")).toBe("ja");
    expect(normalizeContentLang("en")).toBe("en");
    expect(normalizeContentLang("zh")).toBeUndefined();
  });

  it("値があればそれを使い、null なら付けず、渡さなければ本文から判定する", () => {
    expect(resolveContentLang("zh-TW", JA)).toBe("zh-Hant");
    expect(resolveContentLang(null, ZH_HANS)).toBeUndefined();
    expect(resolveContentLang(undefined, ZH_HANS)).toBe("zh-Hans");
    expect(resolveContentLang("zh", ZH_HANT)).toBe("zh-Hant");
    expect(resolveContentLang("zh", JA)).toBe("zh");
    expect(resolveContentLang("  ", ZH_HANS)).toBe("zh-Hans");
  });
});

const evidence = (overrides: Partial<EvidenceListItem> = {}): EvidenceListItem => ({
  key: "e1",
  fileName: "文書 A",
  text: ZH_HANS,
  usedInAnswer: null,
  answerUse: null,
  score: null,
  rerankScore: null,
  ...overrides,
});

function html(node: ReactNode): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(node);
  return host;
}

const langOf = (host: HTMLElement, selector: string) => host.querySelector(selector)?.getAttribute("lang") ?? null;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("共有の部品は利用者の内容に lang を付ける", () => {
  it("MarkdownText・ExcerptContent は本文から判定し、lang の値・null を優先する", () => {
    expect(langOf(html(<MarkdownText text={ZH_HANS} />), "[data-markdown-text]")).toBe("zh-Hans");
    expect(langOf(html(<MarkdownText text={ZH_HANS} lang={null} />), "[data-markdown-text]")).toBeNull();
    expect(langOf(html(<MarkdownText text={JA} lang="zh-TW" />), "[data-markdown-text]")).toBe("zh-Hant");
    expect(langOf(html(<ExcerptContent text={ZH_HANT} testId="excerpt" />), '[data-testid="excerpt"]')).toBe("zh-Hant");
  });

  it("判定できない本文には lang を付けない（画面の言語のまま）", () => {
    expect(langOf(html(<MarkdownText text="東京都港区" />), "[data-markdown-text]")).toBeNull();
  });

  it("質問の吹き出し（ChatUserMessage・ChatTurn）", () => {
    expect(langOf(html(<ChatUserMessage>{ZH_HANS}</ChatUserMessage>), "[lang]")).toBe("zh-Hans");
    expect(langOf(html(<ChatTurn question={JA} />), "[lang]")).toBe("ja");
    expect(langOf(html(<ChatTurn question={<span>{ZH_HANS}</span>} />), "[lang]")).toBeNull();
    expect(langOf(html(<ChatTurn question={<span>{ZH_HANS}</span>} questionLang="zh-Hant" />), "[lang]")).toBe("zh-Hant");
  });

  it("回答の本文（AnswerSections）は段落・項目の文から判定する", () => {
    const host = html(
      <AnswerSections
        blocks={[
          { kind: "paragraph", key: "p", content: "要点" },
          { kind: "section", key: "s", title: "要点", entries: [{ key: "e", content: <b>x</b>, passageText: ZH_HANS }] },
        ]}
      />
    );
    expect(langOf(host, '[data-testid="answer-text"]')).toBe("zh-Hans");
  });

  it("節に分けない回答の本文（AnswerPlainText）", () => {
    expect(langOf(html(<AnswerPlainText text={ZH_HANT} testId="plain" />), '[data-testid="plain"]')).toBe("zh-Hant");
    expect(langOf(html(<AnswerPlainText text={ZH_HANT} lang={null} testId="plain" />), '[data-testid="plain"]')).toBeNull();
  });

  it("根拠の抜粋（EvidenceCard）は item.lang か本文から判定する", () => {
    const card = (item: EvidenceListItem) =>
      html(
        <ul>
          <EvidenceCard item={item} index={0} />
        </ul>
      );
    expect(langOf(card(evidence()), '[data-testid="citation-text"]')).toBe("zh-Hans");
    expect(langOf(card(evidence({ lang: "zh-HK" })), '[data-testid="citation-text"]')).toBe("zh-HK");
    expect(langOf(card(evidence({ text: JA })), '[data-testid="citation-text"]')).toBe("ja");
  });

  it("文書のプレビューの本文（DocumentPreview の text）", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<DocumentPreview kind="text" fileName="a.txt" loadText={async () => ZH_HANT} textKey="a" />);
    });
    expect(langOf(host, '[data-testid="preview-text"]')).toBe("zh-Hant");
    await act(async () => root.unmount());
    host.remove();
  });
});
