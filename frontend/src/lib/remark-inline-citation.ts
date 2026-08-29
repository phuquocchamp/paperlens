/**
 * remark-inline-citation — an mdast-layer remark plugin that turns citation
 * markers like `[C3]`, `[F2]`, `[T1]` (and groups like `[C1, F2]`) inside plain
 * TEXT nodes into clickable citation chips.
 *
 * Design (per CONTRACT.md §3/§6 and the P0 build sheet):
 *   - Uses MARKER_RE / PARTIAL_MARKER_RE from `citation-markers.ts` VERBATIM —
 *     never re-authored here (the Rev-3 divergent-regex bug guard).
 *   - Only visits `text` nodes. `code` / `inlineCode` are distinct mdast node
 *     types and are never visited, so fenced/inline code is immune for free.
 *   - Skips text whose parent is a `link`, so `[text](url)` is never chipped.
 *   - Emits custom `inlineCitation` nodes carrying `data.hName='span'` +
 *     `data.hProperties`; mdast-util-to-hast (remark-rehype) renders them as
 *     `<span data-citation-marker=...>`. The renderer resolves the marker: if it
 *     maps to a known citation/figure/table it becomes clickable; otherwise it
 *     degrades to the plain bracket text.
 *   - Mid-stream, a dangling/incomplete marker tail (e.g. `[C`, `[C1`) at the
 *     very end of the document is hidden via PARTIAL_MARKER_RE — but ONLY on the
 *     last text node and ONLY when `streaming` is set, so a legitimate trailing
 *     `[` before bold/emphasis in a finished message is never eaten.
 */

import type { Root, Text, Parent, RootContent } from "mdast";
import { visit } from "unist-util-visit";
import {
  newMarkerRe,
  newPartialMarkerRe,
  splitMarkerGroup,
} from "./citation-markers";

export interface RemarkInlineCitationOptions {
  /** True while the message is still streaming — enables partial-tail hiding. */
  streaming?: boolean;
}

/** Custom inline node injected in place of a matched marker. */
interface InlineCitationNode {
  type: "inlineCitation";
  data: {
    hName: "span";
    hProperties: {
      "data-citation-marker": string; // primary (first) marker, e.g. "C3"
      "data-citation-markers": string; // all markers in the group, comma-joined
      className: string[];
    };
  };
  children: Text[];
}

interface Target {
  node: Text;
  parent: Parent;
  index: number;
}

function makeCitationNode(rawText: string, markers: string[]): InlineCitationNode {
  return {
    type: "inlineCitation",
    data: {
      hName: "span",
      hProperties: {
        "data-citation-marker": markers[0],
        "data-citation-markers": markers.join(","),
        className: ["citation-chip"],
      },
    },
    children: [{ type: "text", value: rawText }],
  };
}

export default function remarkInlineCitation(
  options: RemarkInlineCitationOptions = {},
) {
  const streaming = options.streaming ?? false;

  return (tree: Root): void => {
    // Pass 1: collect candidate text nodes (skipping those inside links), in
    // document order. Read-only — we splice afterwards so indices stay valid.
    const targets: Target[] = [];
    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (parent.type === "link") return; // never chip link label text
      targets.push({ node, parent: parent as Parent, index });
    });

    if (targets.length === 0) return;

    // The last collected text node is the only place a dangling tail can hide.
    const lastTarget = targets[targets.length - 1];

    // Pass 2: process in REVERSE so earlier splice indices remain correct.
    for (let t = targets.length - 1; t >= 0; t--) {
      const { node, parent, index } = targets[t];
      let value = node.value;

      // Hide an incomplete trailing marker mid-stream (last node only).
      if (streaming && node === lastTarget.node) {
        const partial = newPartialMarkerRe();
        value = value.replace(partial, "");
      }

      const markerRe = newMarkerRe(); // fresh RegExp — no shared lastIndex
      const replacement: RootContent[] = [];
      let cursor = 0;
      let match: RegExpExecArray | null;
      let matched = false;

      while ((match = markerRe.exec(value)) !== null) {
        matched = true;
        const start = match.index;
        const end = start + match[0].length;

        if (start > cursor) {
          replacement.push({ type: "text", value: value.slice(cursor, start) });
        }

        const markers = splitMarkerGroup(match[1]); // e.g. ["C1","F2"]
        replacement.push(
          makeCitationNode(match[0], markers) as unknown as RootContent,
        );
        cursor = end;
      }

      // No markers in this node: only rewrite if streaming trimmed the tail.
      if (!matched) {
        if (value !== node.value) node.value = value;
        continue;
      }

      if (cursor < value.length) {
        replacement.push({ type: "text", value: value.slice(cursor) });
      }

      parent.children.splice(index, 1, ...replacement);
    }
  };
}
