/**
 * Unit tests for remark-inline-citation.
 *
 * Runs the REAL unified pipeline (remarkParse → plugin → remarkRehype →
 * rehypeStringify) so `data.hName`/`data.hProperties` are actually exercised —
 * we assert on the produced HTML, not on a hand-built mdast snapshot. No LLM
 * and no jsdom needed.
 *
 * The cases below are the CONTRACT §6 "must not be mis-parsed as citations"
 * set: [1,2], a code fence, arr[1], [text](url), bold — plus [Figure 2] and the
 * positive/streaming cases.
 */

import { describe, it, expect } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";
import remarkInlineCitation, {
  type RemarkInlineCitationOptions,
} from "./remark-inline-citation";

function render(md: string, options?: RemarkInlineCitationOptions): string {
  return unified()
    .use(remarkParse)
    .use(remarkInlineCitation, options ?? {})
    .use(remarkRehype)
    .use(rehypeStringify)
    .processSync(md)
    .toString();
}

const CHIP = "data-citation-marker";

describe("remark-inline-citation — negative cases (never chipped)", () => {
  it("does not chip a plain numeric bracket group [1,2]", () => {
    const html = render("See results [1,2] here.");
    expect(html).not.toContain(CHIP);
    expect(html).toContain("[1,2]");
  });

  it("does not chip inside a fenced code block", () => {
    const html = render("```\nconst x = arr[1]; // [C3]\n```");
    expect(html).not.toContain(CHIP);
    // The literal marker text survives inside <code>.
    expect(html).toContain("[C3]");
  });

  it("does not chip an array index arr[1]", () => {
    const html = render("The value is `arr[1]` in code, or arr[1] inline.");
    expect(html).not.toContain(CHIP);
  });

  it("does not chip a markdown link [text](url)", () => {
    const html = render("Read [the paper](https://example.com/p) now.");
    expect(html).not.toContain(CHIP);
    expect(html).toContain("<a");
    expect(html).toContain("the paper");
  });

  it("does not chip a link whose label looks like a marker [C3](url)", () => {
    const html = render("Open [C3](https://example.com) link.");
    expect(html).not.toContain(CHIP);
    expect(html).toContain("<a");
  });

  it("does not affect bold/emphasis text", () => {
    const html = render("This is **very important** text.");
    expect(html).not.toContain(CHIP);
    expect(html).toContain("<strong>very important</strong>");
  });

  it("scrubs [Figure 2] as plain text — never becomes [F2]", () => {
    const html = render("As shown in [Figure 2] above.");
    expect(html).not.toContain(CHIP);
    expect(html).toContain("[Figure 2]");
    expect(html).not.toContain("F2");
  });
});

describe("remark-inline-citation — positive cases (chipped)", () => {
  it("chips a single citation marker [C3]", () => {
    const html = render("The accuracy was 94% [C3].");
    expect(html).toContain(`${CHIP}="C3"`);
    expect(html).toContain('class="citation-chip"');
    expect(html).toContain("[C3]");
  });

  it("chips figure and table markers [F2] and [T1]", () => {
    const figure = render("See [F2].");
    expect(figure).toContain(`${CHIP}="F2"`);
    const table = render("See [T1].");
    expect(table).toContain(`${CHIP}="T1"`);
  });

  it("chips a marker group [C1, F2] and records all markers", () => {
    const html = render("Both sources agree [C1, F2].");
    expect(html).toContain(`${CHIP}="C1"`);
    expect(html).toContain('data-citation-markers="C1,F2"');
  });

  it("keeps surrounding text intact around a chip", () => {
    const html = render("Before [C3] after.");
    expect(html).toContain("Before ");
    expect(html).toContain(" after.");
  });
});

describe("remark-inline-citation — streaming partial-tail hiding", () => {
  it("hides a dangling '[C' tail while streaming", () => {
    const html = render("The result is 94% [C", { streaming: true });
    expect(html).not.toContain("[C");
  });

  it("hides a dangling '[C1' tail (marker not yet closed)", () => {
    const html = render("Partial marker [C1", { streaming: true });
    // The unclosed marker must not render as a bare bracket tail.
    expect(html).not.toContain("[C1");
  });

  it("does NOT hide a legitimate trailing '[' when not streaming", () => {
    const html = render("A trailing bracket [", { streaming: false });
    expect(html).toContain("[");
  });

  it("still chips a completed marker earlier in a streaming node", () => {
    const html = render("Done [C3] and typing [F", { streaming: true });
    expect(html).toContain(`${CHIP}="C3"`);
    expect(html).not.toContain("[F");
  });
});
