"use client";

/**
 * Streaming-aware Markdown renderer for assistant answers.
 *
 * - `remark-inline-citation` turns `[C3]` markers into citation chips at the
 *   mdast layer (using the shared MARKER_RE); `streaming` enables hiding a
 *   dangling marker tail on the last text node.
 * - `remark-gfm` for LLM-authored markdown tables, wrapped in `overflow-x-auto`
 *   so a wide table scrolls inside its own container (never the page body).
 * - The `span` slot routes to `CitationChip`, which resolves the marker.
 */

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkInlineCitation from "@/lib/remark-inline-citation";
import { CitationChip } from "./citation-chip";
import { stripPipeTables } from "./answer-layout";

/** Heuristic: right-align a cell whose text is numeric (numbers, %, ±, Δ). */
function isNumericCell(children: ReactNode): boolean {
  const text = String(
    Array.isArray(children) ? children.join("") : (children ?? ""),
  ).trim();
  return text !== "" && /^[+\-±Δ]?[\d.,%\s×xΔ+\-–—]*$/.test(text) && /\d/.test(text);
}

const COMPONENTS: Components = {
  span: CitationChip,
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-line px-3 py-2 text-left font-mono text-[0.7rem] font-medium uppercase tracking-wide text-ink-faint">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td
      className={
        "border-b border-line-soft px-3 py-2 align-top text-ink " +
        (isNumericCell(children) ? "text-right font-mono tabular-nums" : "")
      }
    >
      {children}
    </td>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent-brand underline underline-offset-2"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-ink">{children}</strong>
  ),
  code: ({ children, className }) => (
    <code
      className={className ?? "rounded bg-code-bg px-1 py-0.5 font-mono text-[0.85em]"}
    >
      {children}
    </code>
  ),
};

export function Markdown({
  content,
  streaming,
  suppressTables = false,
}: {
  content: string;
  streaming: boolean;
  /**
   * When true, strip GFM pipe-table blocks from the prose before rendering.
   * Set by the parent when the message carries a structured `data-table`
   * (StructuredTable is the source of truth) so the same table never shows
   * twice; citation markers inside a stripped block are preserved.
   */
  suppressTables?: boolean;
}) {
  const source = suppressTables ? stripPipeTables(content) : content;
  return (
    <div className="prose-paperlens text-sm leading-relaxed [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:text-base [&_h2]:font-semibold">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkInlineCitation, { streaming }]]}
        components={COMPONENTS}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
