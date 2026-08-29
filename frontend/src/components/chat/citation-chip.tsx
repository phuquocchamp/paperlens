"use client";

/**
 * In-text citation chip. Rendered in place of the remark plugin's
 * `<span data-citation-marker=...>`. Resolves the marker against the current
 * message's evidence map:
 *   - resolves → a clickable chip that opens the evidence panel (?ev=msgId:marker)
 *   - does not resolve → degrades to the plain bracket text (CONTRACT §6)
 */

import type { ComponentPropsWithoutRef } from "react";
import { useMessageEvidence } from "./evidence-context";
import { useEvidenceParam } from "./use-evidence-param";

type SpanProps = ComponentPropsWithoutRef<"span"> & {
  "data-citation-marker"?: string;
};

export function CitationChip(props: SpanProps) {
  const marker = props["data-citation-marker"];
  const evidence = useMessageEvidence();
  const { open } = useEvidenceParam();

  // Not a citation span (react-markdown also routes ordinary <span>s here).
  if (!marker) {
    return <span {...props} />;
  }

  const resolved = evidence?.markers.get(marker);
  const number = evidence?.numbers.get(marker);

  // Unresolved marker (or no assigned number yet) → plain bracket text. Covers a
  // figure cited before its `data-figure` overwrite arrives (task spec §4).
  if (!evidence || !resolved || number == null) {
    return <span>{props.children}</span>;
  }

  // Displayed label is the sequential NUMBER, not the raw "[C3]" — the underlying
  // marker/id logic is untouched (we still open ?ev=msgId:marker).
  return (
    <button
      type="button"
      data-citation-marker={marker}
      onClick={() => open(evidence.messageId, marker)}
      className="mx-[0.15em] inline-flex size-[1.05em] translate-y-[0.08em] items-center justify-center rounded-[0.25em] bg-primary align-baseline font-mono text-[0.72em] font-semibold leading-none tabular-nums text-primary-foreground transition-opacity hover:opacity-85"
      title={
        resolved.kind === "citation"
          ? `${resolved.data.document_title}, p.${resolved.data.page}`
          : resolved.data.label
      }
    >
      {number}
    </button>
  );
}
