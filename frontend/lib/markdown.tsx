/**
 * Minimal, dependency-free Markdown -> React renderer for weekly `body_markdown`.
 *
 * Scope is deliberately small (headings, paragraphs, lists, blockquotes, bold/
 * italic/code, links). Content originates from our own trusted backend, but we
 * still NEVER use dangerouslySetInnerHTML — everything is rendered as React
 * elements, so raw HTML in the source is shown as text, not executed.
 */

import { Fragment, type ReactNode } from "react";

let keyCounter = 0;
function nextKey(prefix: string): string {
  keyCounter += 1;
  return `${prefix}-${keyCounter}`;
}

/** Inline formatting: **bold**, *italic*, `code`, [text](url). */
function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Order matters: links first, then code, then bold, then italic.
  const pattern =
    /(\[([^\]]+)\]\(([^)\s]+)\))|(`([^`]+)`)|(\*\*([^*]+)\*\*)|(\*([^*]+)\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    if (match[1]) {
      // link
      nodes.push(
        <a
          key={nextKey("a")}
          href={match[3]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline decoration-hairline underline-offset-2 hover:decoration-accent"
        >
          {match[2]}
        </a>,
      );
    } else if (match[4]) {
      nodes.push(
        <code key={nextKey("code")} className="rounded bg-hairline/50 px-1 font-mono text-[0.85em]">
          {match[5]}
        </code>,
      );
    } else if (match[6]) {
      nodes.push(<strong key={nextKey("b")}>{match[7]}</strong>);
    } else if (match[8]) {
      nodes.push(<em key={nextKey("i")}>{match[9]}</em>);
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

function flushList(items: string[], ordered: boolean): ReactNode {
  if (items.length === 0) return null;
  const children = items.map((it) => (
    <li key={nextKey("li")} className="ml-5 list-disc pl-1 leading-body marker:text-muted">
      {renderInline(it)}
    </li>
  ));
  return ordered ? (
    <ol key={nextKey("ol")} className="my-4 list-decimal space-y-1">
      {children}
    </ol>
  ) : (
    <ul key={nextKey("ul")} className="my-4 space-y-1">
      {children}
    </ul>
  );
}

export function Markdown({ source }: { source: string }): JSX.Element {
  const lines = (source || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let listBuffer: string[] = [];
  let listOrdered = false;
  let paragraphBuffer: string[] = [];

  const flushParagraph = () => {
    if (paragraphBuffer.length === 0) return;
    const text = paragraphBuffer.join(" ").trim();
    if (text) {
      blocks.push(
        <p key={nextKey("p")} className="my-4 leading-body">
          {renderInline(text)}
        </p>,
      );
    }
    paragraphBuffer = [];
  };

  const flushBufferedList = () => {
    const node = flushList(listBuffer, listOrdered);
    if (node) blocks.push(node);
    listBuffer = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();

    if (line.trim() === "") {
      flushParagraph();
      flushBufferedList();
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushBufferedList();
      const level = heading[1].length;
      const content = renderInline(heading[2]);
      const cls =
        level === 1
          ? "mt-8 mb-3 font-serif text-3xl font-semibold"
          : level === 2
            ? "mt-7 mb-3 font-serif text-2xl font-semibold"
            : level === 3
              ? "mt-6 mb-2 font-serif text-xl font-semibold"
              : "mt-5 mb-2 font-mono text-xs uppercase tracking-widest text-muted";
      const Tag = (`h${Math.min(level + 1, 6)}` as keyof JSX.IntrinsicElements);
      blocks.push(
        <Tag key={nextKey("h")} className={cls}>
          {content}
        </Tag>,
      );
      continue;
    }

    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flushParagraph();
      flushBufferedList();
      blocks.push(
        <blockquote
          key={nextKey("q")}
          className="my-4 border-l-2 border-accent pl-4 font-serif italic text-muted"
        >
          {renderInline(quote[1])}
        </blockquote>,
      );
      continue;
    }

    const ordered = /^\d+\.\s+(.*)$/.exec(line);
    const bullet = /^[-*+]\s+(.*)$/.exec(line);
    if (ordered || bullet) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (listBuffer.length > 0 && isOrdered !== listOrdered) {
        flushBufferedList();
      }
      listOrdered = isOrdered;
      listBuffer.push((ordered ? ordered[1] : bullet![1]).trim());
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      flushParagraph();
      flushBufferedList();
      blocks.push(<hr key={nextKey("hr")} className="my-6 border-hairline" />);
      continue;
    }

    paragraphBuffer.push(line.trim());
  }

  flushParagraph();
  flushBufferedList();

  return <Fragment>{blocks}</Fragment>;
}
