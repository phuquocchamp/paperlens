/**
 * Minimal GFM-pipe-table parser for structured `data-table` payloads whose
 * `structure_kind === "flat"` (TableEvent.markdown). Structured tables are
 * server-dereferenced from the source paper — NOT model-authored — so the input
 * is a well-formed pipe table; this parser is deliberately small and total
 * (never throws) rather than a full CommonMark implementation.
 *
 * Returns `null` when the text is not a recognizable pipe table (< 2 rows or no
 * header separator), so callers can fall back to a <pre> block.
 */

export interface ParsedTable {
  headers: string[];
  /** Column alignment from the separator row (`:---`, `---:`, `:--:`). */
  align: Array<"left" | "right" | "center" | null>;
  rows: string[][];
}

/** Split one markdown table line into cell strings, honouring `\|` escapes. */
function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i];
    if (ch === "\\" && trimmed[i + 1] === "|") {
      current += "|";
      i++;
      continue;
    }
    if (ch === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  cells.push(current.trim());
  return cells;
}

/** True for a GFM separator row like `| :--- | ---: | :--: |`. */
function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c.trim()));
}

function alignOf(cell: string): "left" | "right" | "center" | null {
  const c = cell.trim();
  const left = c.startsWith(":");
  const right = c.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return null;
}

export function parseMarkdownTable(markdown: string): ParsedTable | null {
  const lines = markdown
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && l.includes("|"));
  if (lines.length < 2) return null;

  const headers = splitRow(lines[0]);
  const separator = splitRow(lines[1]);
  if (!isSeparatorRow(separator)) return null;

  const align = separator.map(alignOf);
  const rows = lines.slice(2).map((line) => {
    const cells = splitRow(line);
    // Pad/truncate each body row to the header width so the grid stays square.
    if (cells.length < headers.length) {
      return [...cells, ...Array(headers.length - cells.length).fill("")];
    }
    return cells.slice(0, headers.length);
  });

  return { headers, align, rows };
}

/** A cell whose content reads as a number (for right-alignment heuristics). */
const NUMERIC_RE = /^[-+]?[$€£]?\s*\d[\d,]*(\.\d+)?\s*%?$/;

export function isNumericCell(value: string): boolean {
  const v = value.trim();
  if (v === "" || v === "-" || v === "—") return false;
  return NUMERIC_RE.test(v);
}

/**
 * Effective alignment for a column: an explicit markdown alignment wins;
 * otherwise a column is right-aligned when every non-empty body cell is numeric.
 */
export function columnAlignment(
  table: ParsedTable,
  col: number,
): "left" | "right" | "center" {
  const explicit = table.align[col];
  if (explicit) return explicit;
  const cells = table.rows
    .map((r) => r[col] ?? "")
    .filter((c) => c.trim() !== "");
  if (cells.length > 0 && cells.every(isNumericCell)) return "right";
  return "left";
}
