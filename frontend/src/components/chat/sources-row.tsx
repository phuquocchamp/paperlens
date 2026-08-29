"use client";

/**
 * Sources row — rendered below the answer body (product preference). Citations
 * still arrive before the text on the wire (CONTRACT §2); only the visual
 * placement is below.
 *
 * A mono "SOURCES" label on its own line, then clickable numbered items aligned
 * in a wrapping grid — a green number badge + "filename · p.N" in mono. The number
 * comes from the shared per-answer marker→number map (task spec §4). Clicking an
 * item opens the evidence panel for that marker.
 */

import type { CitationData } from "@/lib/types";
import { useEvidenceParam } from "./use-evidence-param";
import { useMessageEvidence } from "./evidence-context";
import { NumberBadge } from "./number-badge";

export function SourcesRow({
  messageId,
  citations,
}: {
  messageId: string;
  citations: CitationData[];
}) {
  const { open } = useEvidenceParam();
  const evidence = useMessageEvidence();
  if (citations.length === 0) return null;

  return (
    // Label on its own line, then the source chips aligned to a consistent
    // left edge (a wrapping grid) so every row lines up.
    <div className="mt-5 flex flex-col gap-2">
      <span className="pl-mono-label">Sources</span>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-4 gap-y-2">
        {citations.map((c) => {
          const n = evidence?.numbers.get(c.marker);
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => open(messageId, c.marker)}
              className="group flex items-center gap-1.5 text-left"
            >
              {n != null && <NumberBadge n={n} />}
              <span className="truncate font-mono text-xs text-ink-soft transition-colors group-hover:text-ink">
                <span className="font-medium">{c.filename}</span>
                <span className="text-ink-faint"> · p.{c.page}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
