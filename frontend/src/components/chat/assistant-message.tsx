"use client";

/**
 * Renders one assistant message in protocol order (CONTRACT §2):
 *   Sources row (data-citation)  →  streamed text (with citation chips)  →
 *   figures (candidates preloaded, cited promoted)  →  structured tables.
 *
 * Parts are partitioned by type rather than trusting array order for layout;
 * the marker→evidence map feeds both the in-text chips and the evidence panel.
 */

import { useMemo } from "react";
import type {
  CitationData,
  FigureData,
  PaperLensUIMessage,
  TableData,
} from "@/lib/types";
import {
  buildMarkerNumbers,
  MessageEvidenceProvider,
  type ResolvedEvidence,
} from "./evidence-context";
import { SourcesRow } from "./sources-row";
import { AnswerBody } from "./answer-body";
import { StructuredTable } from "./structured-table";
import { FeedbackRow } from "./feedback-row";

interface Extracted {
  text: string;
  citations: CitationData[];
  figures: FigureData[];
  tables: TableData[];
  markers: Map<string, ResolvedEvidence>;
}

function extract(message: PaperLensUIMessage): Extracted {
  let text = "";
  const citations: CitationData[] = [];
  const figures: FigureData[] = [];
  const tables: TableData[] = [];
  const markers = new Map<string, ResolvedEvidence>();

  for (const part of message.parts) {
    switch (part.type) {
      case "text":
        text += part.text;
        break;
      case "data-citation": {
        const d = part.data;
        citations.push(d);
        markers.set(d.marker, { kind: "citation", data: d });
        break;
      }
      case "data-figure": {
        const d = part.data;
        figures.push(d);
        if (d.marker) markers.set(d.marker, { kind: "figure", data: d });
        break;
      }
      case "data-table": {
        const d = part.data;
        tables.push(d);
        if (d.marker) markers.set(d.marker, { kind: "table", data: d });
        break;
      }
      default:
        // status is transient (onData); notice/error handled by ChatView.
        break;
    }
  }
  return { text, citations, figures, tables, markers };
}

export function AssistantMessage({
  message,
  isStreaming,
}: {
  message: PaperLensUIMessage;
  isStreaming: boolean;
}) {
  const { text, citations, figures, tables, markers } = useMemo(
    () => extract(message),
    [message],
  );
  const numbers = useMemo(() => buildMarkerNumbers(message), [message]);

  return (
    <MessageEvidenceProvider value={{ messageId: message.id, markers, numbers }}>
      <div className="py-5">
        {/*
          Prose with cited figures placed inline near their [F#] marker; the
          leftover figures fall through to the strip. Pipe tables in the prose
          are suppressed when a structured data-table is the source of truth.
        */}
        <AnswerBody
          messageId={message.id}
          text={text}
          figures={figures}
          isStreaming={isStreaming}
          suppressTables={tables.length > 0}
        />

        {tables.map((t) => (
          <StructuredTable key={t.id} table={t} />
        ))}

        {/* Sources render below the answer (per product preference). */}
        <SourcesRow messageId={message.id} citations={citations} />

        {/* Feedback row (copy / thumbs) — stubs; wired in a later phase. */}
        {!isStreaming && text && <FeedbackRow text={text} />}
      </div>
    </MessageEvidenceProvider>
  );
}
