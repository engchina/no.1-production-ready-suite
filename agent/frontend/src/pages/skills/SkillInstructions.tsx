import { Fragment, type ReactNode } from "react";

import {
  parseMarkdownBlocks,
  parseMarkdownInline,
  type MarkdownBlock,
} from "@/lib/markdown-blocks";

/**
 * スキルの指示（Markdown）を読み取り専用で表示する（#1795）。
 *
 * 組み込みのスキルの指示は、Step で進める手順・結果による分岐を見出しと箇条書きで書く。平文のままだと記号が残って
 * 読めないため、見出し・段落・箇条書き（入れ子）・コードの囲み・行の中の `code` / **強調** だけを React の要素で描く
 * （HTML は解釈しない）。見出しの段は、画面の `Section`（h2）の下になるよう h3 から始める。
 * 段落・箇条書きは長文なので `max-w-prose`（README §4「wide 画面の 100% 充填」の例外: 長文段落）。
 */
export function SkillInstructions({ text, testId }: { text: string; testId?: string }) {
  const blocks = parseMarkdownBlocks(text);
  const headingLevels = collectHeadingLevels(blocks);
  const topLevel = headingLevels.length ? Math.min(...headingLevels) : 1;
  return (
    <div className="min-w-0 space-y-3 text-sm leading-6 text-fg" data-testid={testId}>
      {renderBlocks(blocks, topLevel)}
    </div>
  );
}

function collectHeadingLevels(blocks: MarkdownBlock[]): number[] {
  return blocks.flatMap((block) => {
    if (block.kind === "heading") return [block.level];
    if (block.kind === "list") return block.items.flatMap(collectHeadingLevels);
    return [];
  });
}

function renderBlocks(blocks: MarkdownBlock[], topLevel: number): ReactNode {
  return blocks.map((block, index) => {
    const key = `${block.kind}-${index}`;
    switch (block.kind) {
      case "heading":
        return <Heading key={key} rank={block.level - topLevel} text={block.text} />;
      case "paragraph":
        return (
          <p key={key} className="max-w-prose break-words">
            {block.lines.map((line, lineIndex) => (
              <Fragment key={lineIndex}>
                {lineIndex > 0 ? <br /> : null}
                <InlineText text={line} />
              </Fragment>
            ))}
          </p>
        );
      case "list":
        return <List key={key} block={block} topLevel={topLevel} />;
      case "code":
        return (
          <pre key={key} className="overflow-x-auto rounded-md bg-surface-sunken p-3 font-mono text-xs text-fg">
            <code>{block.text}</code>
          </pre>
        );
      default:
        return null;
    }
  });
}

function Heading({ rank, text }: { rank: number; text: string }) {
  const content = <InlineText text={text} />;
  // 最上位の見出し（`##`）は区切り線で大きな区切りを示し、その下（`### Step N` など）は太字だけにする。
  if (rank <= 0) {
    return (
      <h3 className="border-t border-border pt-4 text-sm font-semibold text-fg first:border-t-0 first:pt-0">
        {content}
      </h3>
    );
  }
  const Tag = rank === 1 ? "h4" : rank === 2 ? "h5" : "h6";
  return <Tag className="pt-1 text-sm font-semibold text-fg">{content}</Tag>;
}

function List({
  block,
  topLevel,
}: {
  block: Extract<MarkdownBlock, { kind: "list" }>;
  topLevel: number;
}) {
  const items = block.items.map((item, index) => (
    <li key={index} className="space-y-1 pl-1 marker:text-fg-muted">
      {item.map((child, childIndex) =>
        // 項目の最初の段落は余白の無い文として出す（箇条書きの行の高さをそろえる）。
        child.kind === "paragraph" ? (
          <p key={childIndex} className="break-words">
            {child.lines.map((line, lineIndex) => (
              <Fragment key={lineIndex}>
                {lineIndex > 0 ? <br /> : null}
                <InlineText text={line} />
              </Fragment>
            ))}
          </p>
        ) : (
          <Fragment key={childIndex}>{renderBlocks([child], topLevel)}</Fragment>
        ),
      )}
    </li>
  ));
  if (block.ordered) {
    return (
      <ol start={block.start === 1 ? undefined : block.start} className="max-w-prose list-decimal space-y-1 pl-6">
        {items}
      </ol>
    );
  }
  return <ul className="max-w-prose list-disc space-y-1 pl-5">{items}</ul>;
}

function InlineText({ text }: { text: string }) {
  return (
    <>
      {parseMarkdownInline(text).map((part, index) => {
        if (part.kind === "code") {
          return (
            <code key={index} className="break-words rounded-sm bg-surface-sunken px-1 font-mono text-xs text-fg">
              {part.text}
            </code>
          );
        }
        if (part.kind === "strong") {
          return (
            <strong key={index} className="font-semibold">
              {part.text}
            </strong>
          );
        }
        return <Fragment key={index}>{part.text}</Fragment>;
      })}
    </>
  );
}
