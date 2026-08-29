/**
 * Unit tests for the pure answer-layout helpers (pipe-table stripping + block
 * splitting). Node env, no jsdom, no React.
 */

import { describe, it, expect } from "vitest";
import {
  stripPipeTables,
  splitIntoBlocks,
  blockContainsMarker,
} from "./answer-layout";

const TABLE = [
  "| Model | Params | Acc |",
  "| --- | ---: | ---: |",
  "| BERT | 110M | 88.5 |",
  "| GPT | 1.5B | 91.2 |",
].join("\n");

describe("stripPipeTables", () => {
  it("removes a pipe table but keeps the surrounding prose", () => {
    const md = `Here are the results.\n\n${TABLE}\n\nSee above.`;
    const out = stripPipeTables(md);
    expect(out).not.toMatch(/\| BERT \|/);
    expect(out).not.toMatch(/---/);
    expect(out).toContain("Here are the results.");
    expect(out).toContain("See above.");
  });

  it("leaves prose untouched when there is no pipe table", () => {
    const md = "Just a sentence with a | vertical bar but no separator row.";
    expect(stripPipeTables(md)).toBe(md);
  });

  it("preserves citation markers found inside a stripped table block", () => {
    const md = ["| A [T1] | B |", "| --- | --- |", "| 1 | 2 |"].join("\n");
    const out = stripPipeTables(md);
    expect(out).not.toMatch(/\| 1 \| 2 \|/);
    // The chip's only occurrence survives so buildMarkerNumbers stays consistent.
    expect(out).toContain("[T1]");
  });

  it("does NOT strip a pipe table drawn inside a fenced code block", () => {
    const md = ["```", TABLE, "```"].join("\n");
    expect(stripPipeTables(md)).toBe(md);
  });

  it("strips two separate tables in one document", () => {
    const md = `${TABLE}\n\nMiddle.\n\n${TABLE}`;
    const out = stripPipeTables(md);
    expect(out).not.toMatch(/\| BERT \|/);
    expect(out).toContain("Middle.");
  });
});

describe("splitIntoBlocks", () => {
  it("splits prose into blocks at blank lines", () => {
    const blocks = splitIntoBlocks("First para.\n\nSecond para.\n\nThird.");
    expect(blocks).toEqual(["First para.", "Second para.", "Third."]);
  });

  it("keeps a fenced code block with an internal blank line intact", () => {
    const md = "Intro.\n\n```\nfoo\n\nbar\n```\n\nOutro.";
    const blocks = splitIntoBlocks(md);
    expect(blocks).toHaveLength(3);
    expect(blocks[1]).toBe("```\nfoo\n\nbar\n```");
  });

  it("collapses runs of blank lines and trims trailing blanks", () => {
    expect(splitIntoBlocks("A.\n\n\n\nB.\n\n")).toEqual(["A.", "B."]);
  });

  it("returns an empty array for empty text", () => {
    expect(splitIntoBlocks("")).toEqual([]);
  });
});

describe("blockContainsMarker", () => {
  it("matches a standalone marker", () => {
    expect(blockContainsMarker("See figure [F2] here.", "F2")).toBe(true);
    expect(blockContainsMarker("See figure [F2] here.", "F3")).toBe(false);
  });

  it("matches a marker inside a group", () => {
    expect(blockContainsMarker("Evidence [C1, F2, T1].", "F2")).toBe(true);
  });

  it("does not match an incomplete/streaming marker tail", () => {
    expect(blockContainsMarker("Trailing [F", "F2")).toBe(false);
  });
});
