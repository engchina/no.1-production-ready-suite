import { Fragment } from "react";

import { cn } from "../../lib/utils";

/** 句読点優先で折り返すメッセージ本文。 */
export interface MessageTextProps {
  text: string;
  className?: string;
}

export interface MessageTextSegment {
  text: string;
  separatorBefore: string;
}

const FALLBACK_BOUNDARY =
  /[。！？；]+(?:[」』】）)\]"'”’]*)|[.!?]+(?:[)\]"'”’]*)(?=\s|$)/gu;

/**
 * 通知本文は prose として扱うため、改行・タブ・連続空白を 1 つの空白へ正規化する。
 * 構造化された複数行コンテンツは string ではなく ReactNode で各コンポーネントへ渡す。
 */
export function normalizeMessageText(text: string): string {
  return text.replace(/\s+/gu, " ").trim();
}

function collectFallbackBoundaries(text: string): number[] {
  const boundaries: number[] = [];
  for (const match of text.matchAll(FALLBACK_BOUNDARY)) {
    if (match.index != null) boundaries.push(match.index + match[0].length);
  }
  return boundaries;
}

function collectIntlBoundaries(text: string, locale: string): number[] {
  if (!("Segmenter" in Intl)) return [];
  try {
    const segmenter = new Intl.Segmenter(locale, { granularity: "sentence" });
    const boundaries: number[] = [];
    for (const item of segmenter.segment(text)) {
      let end = item.index + item.segment.length;
      while (end > item.index && /\s/u.test(text[end - 1] ?? "")) end -= 1;
      if (end > item.index && end < text.length) boundaries.push(end);
    }
    return boundaries;
  } catch {
    return [];
  }
}

/**
 * 文末単位へ分割する。Intl.Segmenter の結果へ決定論的 fallback 境界を重ね、
 * 全角セミコロンや Segmenter 非対応環境でも同じ折返し機会を作る。
 */
export function segmentMessageText(text: string, locale = "ja"): MessageTextSegment[] {
  const normalized = normalizeMessageText(text);
  if (!normalized) return [];

  const boundaries = Array.from(
    new Set([
      ...collectIntlBoundaries(normalized, locale),
      ...collectFallbackBoundaries(normalized),
      normalized.length,
    ])
  ).sort((left, right) => left - right);

  const segments: MessageTextSegment[] = [];
  let start = 0;
  for (const end of boundaries) {
    if (end <= start) continue;
    const raw = normalized.slice(start, end);
    const leading = raw.match(/^\s+/u)?.[0] ?? "";
    const value = raw.slice(leading.length).trimEnd();
    if (value) segments.push({ text: value, separatorBefore: leading });
    start = end;
  }

  return segments;
}

/**
 * 文全体を atomic inline box として描画し、幅不足時は文末を優先して折り返す。
 * 単独の長文・URL は max-width 内で break-words され、横方向へはみ出さない。
 */
export function MessageText({ text, className }: MessageTextProps) {
  const segments = segmentMessageText(text);
  if (segments.length === 0) return null;

  return (
    <span className={cn("min-w-0 whitespace-normal", className)} data-message-text>
      {segments.map((segment, index) => (
        <Fragment key={`${index}-${segment.text}`}>
          {segment.separatorBefore}
          <span
            className="inline-block max-w-full break-words align-baseline"
            data-message-sentence
          >
            {segment.text}
          </span>
        </Fragment>
      ))}
    </span>
  );
}
