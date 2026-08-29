"use client";

/**
 * Per-message evidence resolution for citation chips.
 *
 * The remark plugin turns `[C3]` into `<span data-citation-marker="C3">`. That
 * span is purely syntactic — it does not know whether the marker resolves. This
 * context supplies the marker→evidence map (and the message id) so the chip
 * component can either make the marker clickable (opening the evidence panel) or
 * degrade it to plain bracket text when it does not resolve.
 */

import { createContext, useContext } from "react";
import type {
  CitationData,
  FigureData,
  PaperLensUIMessage,
  TableData,
} from "@/lib/types";

export type ResolvedEvidence =
  | { kind: "citation"; data: CitationData }
  | { kind: "figure"; data: FigureData }
  | { kind: "table"; data: TableData };

export interface MessageEvidence {
  messageId: string;
  /** Keyed by marker token, e.g. "C3" | "F2" | "T1". */
  markers: Map<string, ResolvedEvidence>;
  /**
   * Marker → sequential display number (1, 2, 3…) for THIS answer. The wire
   * markers ("C1"/"F1") keep their id logic; this map is purely how they are
   * RENDERED — as numbered green badges — in both the in-text chips and the
   * sources row / evidence panel (task spec §4).
   */
  numbers: Map<string, number>;
}

/**
 * Build the per-answer marker→number map. Numbers are assigned in order of first
 * appearance in `message.parts`, skipping parts whose marker is null (a figure
 * candidate has no marker until it is cited). This is the ONE source of the
 * numbering — the in-text badges, the sources row, and the evidence panel's
 * "ALL SOURCES IN THIS ANSWER" list all call it, so they can never disagree.
 */
export function buildMarkerNumbers(
  message: PaperLensUIMessage,
): Map<string, number> {
  const numbers = new Map<string, number>();
  let next = 1;
  for (const part of message.parts) {
    if (
      part.type === "data-citation" ||
      part.type === "data-figure" ||
      part.type === "data-table"
    ) {
      const marker = part.data.marker;
      if (marker && !numbers.has(marker)) {
        numbers.set(marker, next++);
      }
    }
  }
  return numbers;
}

const MessageEvidenceContext = createContext<MessageEvidence | null>(null);

export function MessageEvidenceProvider({
  value,
  children,
}: {
  value: MessageEvidence;
  children: React.ReactNode;
}) {
  return (
    <MessageEvidenceContext.Provider value={value}>
      {children}
    </MessageEvidenceContext.Provider>
  );
}

export function useMessageEvidence(): MessageEvidence | null {
  return useContext(MessageEvidenceContext);
}
