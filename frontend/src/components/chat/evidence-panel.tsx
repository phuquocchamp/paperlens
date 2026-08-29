"use client";

/**
 * Evidence panel container — a CONTEXTUAL primitive, not a fixed third column
 * (CONTRACT §6). Opens on citation/figure click (URL `?ev=msgId:marker`), closes
 * on X, Esc, or Back. Renders as a right-side panel at ≥ md and as a bottom
 * Sheet below md; both host the same `EvidenceContent`.
 *
 * The variant is chosen with a JS media-query hook (`useIsMobile`), NOT CSS:
 * the Sheet portals to <body>, so a `md:hidden` wrapper would not hide it — both
 * would show at once on desktop.
 */

import { useEffect } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import type { ResolvedEvidence } from "./evidence-context";
import { EvidenceContent, type AnswerSource } from "./evidence-content";
import { NumberBadge } from "./number-badge";
import { useEvidenceParam } from "./use-evidence-param";

export function EvidencePanel({
  evidence,
  sources = [],
  activeNumber,
  onSelectSource,
}: {
  evidence: ResolvedEvidence | null;
  sources?: AnswerSource[];
  activeNumber?: number;
  onSelectSource?: (marker: string) => void;
}) {
  const { close } = useEvidenceParam();
  const isMobile = useIsMobile();
  const open = evidence != null;

  // Esc closes (Back is handled by the URL history entry).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  // Mobile (< md): bottom Sheet.
  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={(o) => !o && close()}>
        <SheetContent side="bottom" className="max-h-[85vh] overflow-y-auto bg-surface">
          <SheetTitle className="mb-3 flex items-center gap-2 font-heading">
            {activeNumber != null && <NumberBadge n={activeNumber} />}
            Evidence
          </SheetTitle>
          {evidence && (
            <EvidenceContent
              evidence={evidence}
              sources={sources}
              activeNumber={activeNumber}
              onSelectSource={onSelectSource}
            />
          )}
        </SheetContent>
      </Sheet>
    );
  }

  // Desktop (≥ md): inline right-side panel. Nothing renders when closed.
  if (!open) return null;

  return (
    <aside
      className="flex w-[380px] shrink-0 flex-col border-l border-line bg-surface"
      aria-label="Evidence"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 font-heading text-sm font-semibold text-ink">
          {activeNumber != null && <NumberBadge n={activeNumber} />}
          Evidence
        </h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={close}
          aria-label="Close evidence"
          className="size-7 text-ink-faint hover:text-ink"
        >
          <X className="size-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {evidence && (
          <EvidenceContent
            evidence={evidence}
            sources={sources}
            activeNumber={activeNumber}
            onSelectSource={onSelectSource}
          />
        )}
      </div>
    </aside>
  );
}
