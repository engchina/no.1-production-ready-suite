/**
 * 回答本文の構成（#651）。回答エンジン（rag_engine の `grounded.render()`）は本文を決まった書式で組み立てる:
 * 要約の段落、節の見出し（「確認できる内容」「操作手順（機能名）」など）、「・」か「N. 」で始まる説明、
 * 説明の直後の「根拠：ファイル名 p.N」の行。画面はこの書式で節・説明・根拠に分けて出す。
 * 書式の見出しが 1 つも無い本文（根拠不足の案内など）は null（そのままの文字列で出す）。
 */

export type AnswerTextEntry =
  | { kind: "item"; text: string; citations: string[] }
  | { kind: "text"; text: string };

export type AnswerTextBlock =
  | { kind: "paragraph"; text: string }
  | { kind: "section"; title: string; ordered: boolean; entries: AnswerTextEntry[] };

// rag_engine の grounded.py の RULES_SECTION_TITLE / QUOTE_ONLY_LABEL / GAPS_SECTION_TITLE と手順の見出し。
const SECTION_TITLE =
  /^(?:確認できる内容|資料の記載（今回への適用は未確認）|資料からは確認できない点|(?:操作|確認)手順(?:（.+）)?)$/;
const CITATION = /^根拠：/;
const BULLET = /^・/;
const NUMBERED = /^\d+\.\s/;

export function parseAnswerText(text: string): AnswerTextBlock[] | null {
  const lines = text.split("\n");
  if (!lines.some((line) => SECTION_TITLE.test(line.trim()))) return null;
  const blocks: AnswerTextBlock[] = [];
  let section: Extract<AnswerTextBlock, { kind: "section" }> | null = null;
  let paragraph: string[] = [];
  const flushParagraph = () => {
    const joined = paragraph.join("\n").trim();
    paragraph = [];
    if (!joined) return;
    if (section) section.entries.push({ kind: "text", text: joined });
    else blocks.push({ kind: "paragraph", text: joined });
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      continue;
    }
    if (SECTION_TITLE.test(line)) {
      flushParagraph();
      section = { kind: "section", title: line, ordered: true, entries: [] };
      blocks.push(section);
      continue;
    }
    if (section && (BULLET.test(line) || NUMBERED.test(line))) {
      flushParagraph();
      if (!NUMBERED.test(line)) section.ordered = false;
      section.entries.push({ kind: "item", text: line.replace(BULLET, "").replace(NUMBERED, ""), citations: [] });
      continue;
    }
    const last = section?.entries[section.entries.length - 1];
    if (CITATION.test(line) && last?.kind === "item" && paragraph.length === 0) {
      last.citations.push(line);
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  for (const block of blocks) {
    if (block.kind === "section" && !block.entries.some((entry) => entry.kind === "item")) {
      block.ordered = false;
    }
  }
  return blocks;
}
