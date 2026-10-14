/**
 * スキルの指示（Markdown）を、表示に要るだけのブロックに分ける（#1795）。
 *
 * 扱うのは見出し（`#`〜`######`）・段落・番号付き / 記号の箇条書き（字下げで入れ子）・コードの囲み（```）と、
 * 行の中の `code` と **強調** だけ。HTML・リンク・表などは解釈せず、文字のまま段落に残す
 * （描画は React の要素だけで行い、innerHTML を使わない）。
 */

export type MarkdownBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; start: number; items: MarkdownBlock[][] }
  | { kind: "code"; text: string };

export type MarkdownInline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; text: string };

const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const FENCE = /^ {0,3}```/;
const LIST_ITEM = /^( {0,3})([-*+]|\d{1,9}[.)])( {1,4}|\t)(.*)$/;

function isBlank(line: string): boolean {
  return line.trim() === "";
}

function indentOf(line: string): number {
  return line.length - line.replace(/^ +/, "").length;
}

function startsBlock(line: string): boolean {
  return HEADING.test(line) || FENCE.test(line) || LIST_ITEM.test(line);
}

function isOrderedMarker(marker: string): boolean {
  return /^\d/.test(marker);
}

/** Markdown の本文をブロックの並びにする。 */
export function parseMarkdownBlocks(source: string): MarkdownBlock[] {
  return parseLines(source.replace(/\r\n?/g, "\n").split("\n"));
}

function parseLines(lines: string[]): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (isBlank(line)) {
      index += 1;
      continue;
    }
    if (FENCE.test(line)) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index] ?? "")) {
        body.push(lines[index] ?? "");
        index += 1;
      }
      index += 1; // 閉じの ``` を読み飛ばす（無ければ末尾まで）。
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 1, text: heading[2] ?? "" });
      index += 1;
      continue;
    }
    const item = LIST_ITEM.exec(line);
    if (item) {
      index = parseList(lines, index, blocks);
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index] ?? "";
      if (isBlank(current) || (paragraph.length > 0 && startsBlock(current))) break;
      paragraph.push(current.trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", lines: paragraph });
  }
  return blocks;
}

/** `start` の行から 1 つの箇条書きを読み、次に読む行の位置を返す。 */
function parseList(lines: string[], start: number, blocks: MarkdownBlock[]): number {
  const first = LIST_ITEM.exec(lines[start] ?? "");
  const ordered = isOrderedMarker(first?.[2] ?? "");
  const items: MarkdownBlock[][] = [];
  let index = start;
  while (index < lines.length) {
    const match = LIST_ITEM.exec(lines[index] ?? "");
    if (!match || isOrderedMarker(match[2] ?? "") !== ordered) break;
    const [, indent = "", marker = "", gap = "", text = ""] = match;
    // 項目の中身の字下げ（記号の後の空白まで）。これ以上字下げした行は、この項目の続き・入れ子。
    const contentIndent = indent.length + marker.length + (gap === "\t" ? 1 : gap.length);
    const body = [text];
    index += 1;
    while (index < lines.length) {
      const current = lines[index] ?? "";
      if (isBlank(current)) {
        let next = index + 1;
        while (next < lines.length && isBlank(lines[next] ?? "")) next += 1;
        if (next < lines.length && indentOf(lines[next] ?? "") >= contentIndent) {
          body.push("");
          index += 1;
          continue;
        }
        break;
      }
      if (indentOf(current) >= contentIndent) {
        body.push(current.slice(contentIndent));
        index += 1;
        continue;
      }
      // 字下げの無い続きの行（段落の続き）は、この項目の文に足す。
      if (!startsBlock(current) && !isBlank(body[body.length - 1] ?? "")) {
        body.push(current.trim());
        index += 1;
        continue;
      }
      break;
    }
    items.push(parseLines(body));
    // 項目の間の空行は、次が同じ種類の項目なら読み飛ばす。
    let next = index;
    while (next < lines.length && isBlank(lines[next] ?? "")) next += 1;
    const following = LIST_ITEM.exec(lines[next] ?? "");
    if (next !== index && following && isOrderedMarker(following[2] ?? "") === ordered) index = next;
  }
  const startNumber = ordered ? Number.parseInt(first?.[2] ?? "1", 10) : 1;
  blocks.push({ kind: "list", ordered, start: Number.isFinite(startNumber) ? startNumber : 1, items });
  return index;
}

/** 行の中の `code` と **強調** を分ける（ほかの記号は文字のまま）。 */
export function parseMarkdownInline(text: string): MarkdownInline[] {
  const parts: MarkdownInline[] = [];
  const pattern = /(`+)([\s\S]*?[^`])\1(?!`)|\*\*(?=\S)([\s\S]*?\S)\*\*/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    if (match[1] !== undefined) {
      parts.push({ kind: "code", text: (match[2] ?? "").replace(/^ (.*) $/, "$1") });
    } else {
      parts.push({ kind: "strong", text: match[3] ?? "" });
    }
    last = at + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}
