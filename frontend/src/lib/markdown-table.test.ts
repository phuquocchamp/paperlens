/**
 * Unit tests for the flat-table markdown parser used by StructuredTable and the
 * evidence panel. Pure functions, node env — no jsdom, no React.
 */

import { describe, it, expect } from "vitest";
import {
  parseMarkdownTable,
  isNumericCell,
  columnAlignment,
} from "./markdown-table";

describe("parseMarkdownTable", () => {
  it("parses a basic pipe table with header + rows", () => {
    const md = [
      "| Model | Params | Acc |",
      "| --- | ---: | ---: |",
      "| BERT | 110M | 88.5 |",
      "| GPT | 1.5B | 91.2 |",
    ].join("\n");
    const t = parseMarkdownTable(md);
    expect(t).not.toBeNull();
    expect(t!.headers).toEqual(["Model", "Params", "Acc"]);
    expect(t!.rows).toHaveLength(2);
    expect(t!.rows[0]).toEqual(["BERT", "110M", "88.5"]);
    expect(t!.align).toEqual([null, "right", "right"]);
  });

  it("returns null when there is no separator row", () => {
    expect(parseMarkdownTable("| a | b |\n| c | d |")).toBeNull();
  });

  it("returns null for non-table text", () => {
    expect(parseMarkdownTable("just a sentence")).toBeNull();
    expect(parseMarkdownTable("")).toBeNull();
  });

  it("handles tables without outer pipes", () => {
    const t = parseMarkdownTable("a | b\n--- | ---\n1 | 2");
    expect(t!.headers).toEqual(["a", "b"]);
    expect(t!.rows[0]).toEqual(["1", "2"]);
  });

  it("honours escaped pipes inside cells", () => {
    const t = parseMarkdownTable("| x | y |\n| --- | --- |\n| a \\| b | c |");
    expect(t!.rows[0]).toEqual(["a | b", "c"]);
  });

  it("reads center/left/right alignment markers", () => {
    const t = parseMarkdownTable("| a | b | c |\n| :--- | :--: | ---: |\n| 1 | 2 | 3 |");
    expect(t!.align).toEqual(["left", "center", "right"]);
  });

  it("pads short body rows to the header width", () => {
    const t = parseMarkdownTable("| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |");
    expect(t!.rows[0]).toEqual(["1", "2", ""]);
  });
});

describe("isNumericCell", () => {
  it("accepts plain and formatted numbers", () => {
    for (const v of ["12", "-3.5", "+7", "1,024", "98.6%", "$1,299", "0"]) {
      expect(isNumericCell(v)).toBe(true);
    }
  });
  it("rejects text and empty/dash placeholders", () => {
    for (const v of ["BERT", "", "-", "—", "N/A", "12ms"]) {
      expect(isNumericCell(v)).toBe(false);
    }
  });
});

describe("columnAlignment", () => {
  it("infers right alignment for all-numeric columns", () => {
    const t = parseMarkdownTable(
      "| name | score |\n| --- | --- |\n| a | 1 |\n| b | 2.5 |",
    )!;
    expect(columnAlignment(t, 0)).toBe("left");
    expect(columnAlignment(t, 1)).toBe("right");
  });

  it("lets an explicit markdown alignment win over the numeric heuristic", () => {
    const t = parseMarkdownTable(
      "| name | score |\n| --- | :--- |\n| a | 1 |\n| b | 2 |",
    )!;
    // Column 1 is all-numeric but explicitly left-aligned.
    expect(columnAlignment(t, 1)).toBe("left");
  });
});
