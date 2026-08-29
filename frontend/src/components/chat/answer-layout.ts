/**
 * Pure, React-free helpers for laying out an assistant answer:
 *
 *  - `stripPipeTables` removes GFM pipe-table blocks from prose. Used when the
 *    message also carries a structured `data-table` (the source of truth), so the
 *    UI never shows the same table twice (duplicate-table fix). Any citation
 *    markers found inside a stripped block are preserved so their chips — and the
 *    per-answer numbering in `buildMarkerNumbers` — never point at nothing.
 *
 *  - `splitIntoBlocks` splits markdown into top-level blocks at blank lines so a
 *    cited figure can be injected right after the block that references its
 *    `[F#]` marker (inline-figure fix).
 *
 *  - `blockContainsMarker` reports whether a block references a given marker.
 *
 * Both scanners are FENCE-AWARE: lines inside a ``` / ~~~ fenced code block are
 * opaque — never a block boundary, never a table row — so a pipe table drawn
 * inside a code sample is left intact and a fenced block with an internal blank
 * line is not torn in half.
 *
 * Node-safe: imports only the pure marker helpers via a RELATIVE path, so the
 * node-env Vitest suite resolves it without the `@/` alias.
 */

import { newMarkerRe, splitMarkerGroup } from "../../lib/citation-markers";

/**
 * Per-line mask: `true` when the line is part of a fenced code block, INCLUDING
 * the opening and closing fence delimiter lines. A fence opened with N backticks
 * (or tildes) is closed by a run of the same character at least N long.
 */
function fenceMask(lines: string[]): boolean[] {
  const mask: boolean[] = new Array(lines.length).fill(false);
  let open: string | null = null; // the opening delimiter run, e.g. "```"
  for (let i = 0; i < lines.length; i++) {
    const m = /^\s*(`{3,}|~{3,})/.exec(lines[i]);
    if (open == null) {
      if (m) {
        open = m[1];
        mask[i] = true; // opening delimiter counts as inside
      }
    } else {
      mask[i] = true; // content and the closing delimiter count as inside
      if (m && m[1][0] === open[0] && m[1].length >= open.length) {
        open = null; // fence closes after this line
      }
    }
  }
  return mask;
}

/** Split one pipe-table row into trimmed cells, honouring `\|` escapes. */
function splitCells(line: string): string[] {
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
function isSeparatorRow(line: string): boolean {
  if (!line.includes("-")) return false;
  const cells = splitCells(line);
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c.trim()));
}

/** All citation markers (`[T1]`, `[C1, F2]`, …) appearing in a chunk of text. */
function extractMarkerText(text: string): string {
  const re = newMarkerRe();
  const found: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) found.push(m[0]);
  return found.join(" ");
}

/**
 * Remove GFM pipe-table blocks from `markdown`. A block is a header row
 * containing `|`, an immediately-following separator row, and any contiguous
 * body rows containing `|` — none of them inside a fenced code block. A stripped
 * block is replaced by the citation markers it contained (if any), so a chip
 * whose only occurrence was inside the table still renders; otherwise it is
 * dropped entirely.
 */
export function stripPipeTables(markdown: string): string {
  const lines = markdown.split("\n");
  const mask = fenceMask(lines);
  const out: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const header = lines[i];
    const sep = lines[i + 1];
    const startsTable =
      !mask[i] &&
      !mask[i + 1] &&
      header.includes("|") &&
      header.trim() !== "" &&
      sep != null &&
      isSeparatorRow(sep);

    if (!startsTable) {
      out.push(header);
      continue;
    }

    // Consume the header + separator + contiguous body rows.
    const block: string[] = [header, sep];
    let j = i + 2;
    while (
      j < lines.length &&
      !mask[j] &&
      lines[j].trim() !== "" &&
      lines[j].includes("|")
    ) {
      block.push(lines[j]);
      j++;
    }

    const markers = extractMarkerText(block.join("\n"));
    if (markers) out.push(markers);
    i = j - 1; // continue after the table block
  }

  return out.join("\n");
}

/**
 * Split markdown into top-level blocks at blank lines. Blank lines inside a
 * fenced code block are NOT boundaries, so fenced blocks stay whole. Returns the
 * non-empty blocks in document order.
 */
export function splitIntoBlocks(text: string): string[] {
  const lines = text.split("\n");
  const mask = fenceMask(lines);
  const blocks: string[] = [];
  let current: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const isBoundary = lines[i].trim() === "" && !mask[i];
    if (isBoundary) {
      if (current.length > 0) {
        blocks.push(current.join("\n"));
        current = [];
      }
      continue;
    }
    current.push(lines[i]);
  }
  if (current.length > 0) blocks.push(current.join("\n"));
  return blocks;
}

/** Whether `block` references `marker` (e.g. "F2"), including inside a group. */
export function blockContainsMarker(block: string, marker: string): boolean {
  const re = newMarkerRe();
  let m: RegExpExecArray | null;
  while ((m = re.exec(block)) !== null) {
    if (splitMarkerGroup(m[1]).includes(marker)) return true;
  }
  return false;
}
