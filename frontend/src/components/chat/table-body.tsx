"use client";

/**
 * Shared renderer for a structured `data-table` body, used by both the inline
 * StructuredTable (in the answer flow) and the evidence panel so the two agree
 * on styling (mono header, right-aligned numeric columns, bordered cells).
 *
 *   - structure_kind "spanned" → server-provided HTML (rowspan/colspan kept).
 *     The HTML is server-dereferenced from the source document, never model
 *     output, so it is trusted; we style it with descendant selectors rather
 *     than re-parsing it.
 *   - structure_kind "flat"    → GFM markdown, parsed here into a real <table>.
 *     A non-parseable payload degrades to a mono <pre> (never a crash).
 *
 * Callers own the `overflow-x-auto` scroll container.
 */

import type { TableData } from "@/lib/types";
import { columnAlignment, parseMarkdownTable } from "@/lib/markdown-table";
import { cn } from "@/lib/utils";

/** Tailwind classes applied to a real bordered table (flat + spanned share it). */
const CELL =
  "[&_td]:border [&_td]:border-line-soft [&_td]:px-2.5 [&_td]:py-1.5 " +
  "[&_th]:border [&_th]:border-line-soft [&_th]:bg-line-soft/60 [&_th]:px-2.5 [&_th]:py-1.5 " +
  "[&_th]:text-left [&_th]:font-mono [&_th]:text-[0.7rem] [&_th]:font-medium " +
  "[&_th]:uppercase [&_th]:tracking-wide [&_th]:text-ink";

function FlatTable({ markdown }: { markdown: string }) {
  const parsed = parseMarkdownTable(markdown);
  if (!parsed) {
    return (
      <pre className="whitespace-pre-wrap p-3 font-mono text-xs text-ink">
        {markdown}
      </pre>
    );
  }
  const alignClass = (a: "left" | "right" | "center") =>
    a === "right" ? "text-right tabular-nums" : a === "center" ? "text-center" : "text-left";

  return (
    <table className={cn("w-full border-collapse text-sm text-ink", CELL)}>
      <thead>
        <tr>
          {parsed.headers.map((h, i) => (
            <th key={i} className={alignClass(columnAlignment(parsed, i))}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {parsed.rows.map((row, r) => (
          <tr key={r}>
            {row.map((cell, c) => (
              <td key={c} className={alignClass(columnAlignment(parsed, c))}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TableBody({ table }: { table: TableData }) {
  if (table.structure_kind === "spanned" && table.html) {
    // Trusted server HTML: style-only wrapper, no re-parse.
    return (
      <div
        className={cn("w-full text-sm text-ink [&_table]:w-full [&_table]:border-collapse", CELL)}
        dangerouslySetInnerHTML={{ __html: table.html }}
      />
    );
  }
  return <FlatTable markdown={table.markdown ?? ""} />;
}
