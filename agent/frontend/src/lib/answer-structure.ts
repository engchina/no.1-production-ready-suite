import type { AnswerEvidenceData } from "@/lib/answer-evidence";

/**
 * 回答の本文の節（#2328）。RAG のチャットの回答と同じく、要約の段落 →「資料により確認できた事項」の箇条書き
 * （各項目の下に「根拠：文書 p.N」）→「資料から確認できなかった事項」の形で出すための、表示だけの組み立て。
 *
 * - 本文の文は書き換えない（AI で直さない）。並べ方と、根拠の ID の印（「【証拠 ID: …】」）を根拠の行にする
 *   ことだけをする。根拠の行は backend の成果物「回答の根拠」の段落ごとの根拠（`passages`。最終の検証が段落を
 *   裏付けた根拠と、段落が書いた ID の印を最終の検証と同じ照合で引き当てた根拠）から決める。
 * - 「資料から確認できなかった事項」は、最終の検証が回答の末尾に足した節（見出しは成果物の `notice.heading`）。
 *   画面では回答の確かめに同じ項目を出し直さない。
 * - 段落に根拠を 1 つも結び付けられないときは null（呼び出し側は今までの Markdown の描き方にする）。
 */

export interface StructuredEntry {
  key: string;
  text: string;
  chunkIds: string[];
  /** 箇条書きの印を付けない補足の行。 */
  muted?: boolean;
}

export type StructuredBlock =
  | { kind: "paragraph"; key: string; text: string; chunkIds: string[] }
  /** 表・コード・見出しの続きなど、段落にできない Markdown の塊。 */
  | { kind: "markdown"; key: string; text: string; chunkIds: string[] }
  | { kind: "section"; key: string; title: string | null; ordered: boolean; entries: StructuredEntry[] };

const BULLET = /^\s*(?:[-*+・•]|(\d{1,3})[.)．）])\s+/;
const HEADING = /^\s*(?:#{1,6}\s+(.+?)\s*#*|\*\*([^*\n]{1,60})\*\*\s*[:：]?|【([^】\n]{1,30})】\s*[:：]?)\s*$/;
const MARKDOWN_BLOCK = /^\s*(?:\||```|~~~|>|\s{2,}[-*+]\s)/;

/** 回答の節（`notice.heading`）。最終の検証の成果物の内容から読む。 */
export function noticeHeading(validation: Record<string, unknown> | null): string | null {
  const notice = validation?.notice;
  if (!notice || typeof notice !== "object" || Array.isArray(notice)) return null;
  const heading = (notice as Record<string, unknown>).heading;
  return typeof heading === "string" && heading ? heading : null;
}

function headingText(line: string): string | null {
  const match = HEADING.exec(line);
  if (!match) return null;
  return (match[1] ?? match[2] ?? match[3] ?? "").trim() || null;
}

/** 本文から根拠の ID の印を除いた表示の文（印の前後の空白をつめる）。 */
function displayText(raw: string, markers: readonly string[]): string {
  let value = raw;
  for (const marker of markers) value = value.split(marker).join("");
  return value
    .replace(/[ \t\u3000]+([。、．，,.）)」』])/g, "$1")
    .replace(/[ \t\u3000]{2,}/g, " ")
    .trim();
}

function chunkIdsOf(raw: string, evidence: AnswerEvidenceData): string[] {
  const ids: string[] = [];
  for (const passage of evidence.passages) {
    if (!raw.includes(passage.text)) continue;
    for (const id of passage.chunkIds) if (!ids.includes(id)) ids.push(id);
  }
  for (const marker of evidence.markers) {
    if (!raw.includes(marker.text)) continue;
    for (const id of marker.chunkIds) if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

/** 空行で区切った本文の塊。 */
function chunks(text: string): string[][] {
  const result: string[][] = [];
  let current: string[] = [];
  for (const line of text.split("\n")) {
    if (line.trim()) {
      current.push(line);
    } else if (current.length) {
      result.push(current);
      current = [];
    }
  }
  if (current.length) result.push(current);
  return result;
}

/** 本文と「資料で確認できず除いた内容」の節を分ける。 */
function splitNotice(answer: string, heading: string | null): { body: string; notice: string[] } {
  if (!heading) return { body: answer, notice: [] };
  const marker = `**${heading}**`;
  const at = answer.indexOf(marker);
  if (at < 0) return { body: answer, notice: [] };
  return {
    body: answer.slice(0, at).trim(),
    notice: answer
      .slice(at + marker.length)
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
  };
}

interface Line {
  raw: string;
  text: string;
  chunkIds: string[];
}

/** 回答の本文を節に組み立てる。結び付けられないときは null。 */
export function structureAnswer(
  answer: string,
  evidence: AnswerEvidenceData | null,
  heading: string | null,
  titles: { confirmed: string; unconfirmed: string }
): StructuredBlock[] | null {
  if (!evidence || (!evidence.passages.length && !evidence.markers.length)) return null;
  const markers = evidence.markers.map((item) => item.text);
  const { body, notice } = splitNotice(answer, heading);
  const line = (raw: string): Line => ({
    raw,
    text: displayText(raw.replace(BULLET, ""), markers),
    chunkIds: chunkIdsOf(raw, evidence),
  });

  const blocks: StructuredBlock[] = [];
  let ownHeadings = false;
  chunks(body).forEach((lines, index) => {
    const key = `b${index}`;
    if (lines.some((item) => MARKDOWN_BLOCK.test(item))) {
      const raw = lines.join("\n");
      blocks.push({ kind: "markdown", key, text: displayText(raw, markers), chunkIds: chunkIdsOf(raw, evidence) });
      return;
    }
    let rest = lines;
    const title = headingText(lines[0]);
    if (title !== null) {
      ownHeadings = true;
      rest = lines.slice(1);
      if (!rest.length) {
        blocks.push({ kind: "section", key, title, ordered: false, entries: [] });
        return;
      }
    }
    if (rest.every((item) => BULLET.test(item))) {
      const parsed = rest.map(line);
      blocks.push({
        kind: "section",
        key,
        title,
        ordered: rest.every((item) => /^\s*\d/.test(item)),
        entries: parsed.map((item, position) => ({ key: `${key}-${position}`, text: item.text, chunkIds: item.chunkIds })),
      });
      return;
    }
    if (title !== null) {
      // 見出しの下の段落は、見出しの節の項目にする（RAG の節と同じ形）。
      const parsed = rest.map(line);
      blocks.push({
        kind: "section",
        key,
        title,
        ordered: false,
        entries: parsed.map((item, position) => ({ key: `${key}-${position}`, text: item.text, chunkIds: item.chunkIds })),
      });
      return;
    }
    const raw = rest.join("\n");
    blocks.push({ kind: "paragraph", key, text: displayText(raw, markers), chunkIds: chunkIdsOf(raw, evidence) });
  });
  merge(blocks);

  const cited = (block: StructuredBlock) =>
    block.kind === "section" ? block.entries.some((entry) => entry.chunkIds.length) : block.chunkIds.length > 0;
  if (!blocks.some(cited)) return null;

  const shaped = ownHeadings ? blocks : withConfirmedSection(blocks, titles.confirmed);
  if (notice.length) {
    shaped.push({
      kind: "section",
      key: "notice",
      title: titles.unconfirmed,
      ordered: false,
      entries: notice.map((item, position) =>
        BULLET.test(item)
          ? { key: `n${position}`, text: item.replace(BULLET, ""), chunkIds: [] }
          : { key: `n${position}`, text: item, chunkIds: [], muted: true }
      ),
    });
  }
  return shaped;
}

/** 空の見出しの節の直後の箇条書きは、その見出しの節にまとめる（見出しと箇条書きの間に空行がある書き方）。 */
function merge(blocks: StructuredBlock[]): void {
  for (let index = blocks.length - 2; index >= 0; index -= 1) {
    const current = blocks[index];
    const next = blocks[index + 1];
    if (current.kind === "section" && current.title && !current.entries.length && next.kind === "section" && !next.title) {
      blocks.splice(index, 2, { ...next, key: current.key, title: current.title });
    }
  }
}

/**
 * 見出しの無い回答: 根拠の付いた最初の塊より前を要約の段落にし、そこから後がすべて根拠の付いた段落・項目なら
 * 「資料により確認できた事項」の箇条書きにまとめる（文の順は変えない）。後ろに根拠の無い段落があれば、見出しは
 * 足さずに段落・箇条書きのまま出す。
 */
function withConfirmedSection(blocks: StructuredBlock[], title: string): StructuredBlock[] {
  const first = blocks.findIndex((block) =>
    block.kind === "section" ? block.entries.some((entry) => entry.chunkIds.length) : block.chunkIds.length > 0
  );
  const lead = blocks.slice(0, first);
  const rest = blocks.slice(first);
  const entries: StructuredEntry[] = [];
  for (const block of rest) {
    if (block.kind === "markdown") return blocks;
    if (block.kind === "paragraph") {
      if (!block.chunkIds.length) return blocks;
      entries.push({ key: block.key, text: block.text, chunkIds: block.chunkIds });
    } else {
      if (block.entries.some((entry) => !entry.chunkIds.length)) return blocks;
      entries.push(...block.entries);
    }
  }
  // 要約が無い（最初の塊から根拠が付く）ときは、最初の段落を要約として残す。
  if (!lead.length && rest[0]?.kind === "paragraph" && entries.length > 1) {
    const [summary, ...others] = entries;
    return [
      { kind: "paragraph", key: summary.key, text: summary.text, chunkIds: summary.chunkIds },
      { kind: "section", key: "confirmed", title, ordered: false, entries: others },
    ];
  }
  return [...lead, { kind: "section", key: "confirmed", title, ordered: false, entries }];
}
