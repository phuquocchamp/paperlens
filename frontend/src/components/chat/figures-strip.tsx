"use client";

/**
 * Figures attached to an answer.
 *
 * A `data-figure` arrives first as display="candidate" — we preload the image
 * immediately with explicit width/height so promotion causes NO layout shift
 * (CONTRACT §6). When the model cites `[F2]` the same figure is re-emitted with
 * display="cited": it is promoted into the answer flow as a COMPACT card
 * (thumbnail left; "Figure N · p.N" + number badge + caption + an "Open in
 * document" link on the right — task spec §5). Remaining candidates sit in a
 * muted "Related figures" strip.
 */

import { useEffect, useRef, useState } from "react";
import { ArrowRight, ImageOff } from "lucide-react";
import type { FigureData } from "@/lib/types";
import { useEvidenceParam } from "./use-evidence-param";
import { useMessageEvidence } from "./evidence-context";
import { NumberBadge } from "./number-badge";
import { cn } from "@/lib/utils";

/** Image slot that reserves the aspect ratio and degrades to a neutral box. */
function Thumb({
  figure,
  className,
}: {
  figure: FigureData;
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const w = figure.width ?? 480;
  const h = figure.height ?? 270;

  // The image is server-rendered, so a broken src can finish loading (and error)
  // BEFORE React hydrates and attaches onError — the event would be missed.
  // Re-check the completed state on mount to catch that race.
  useEffect(() => {
    const img = imgRef.current;
    if (img && img.complete && img.naturalWidth === 0) setBroken(true);
  }, []);

  return (
    <div
      className={cn(
        "flex items-center justify-center overflow-hidden bg-line-soft",
        className,
      )}
      style={{ aspectRatio: `${w} / ${h}` }}
    >
      {broken ? (
        // Degrade to a neutral placeholder — never a giant broken-image icon.
        <span
          className="flex flex-col items-center gap-1 text-ink-faint"
          role="img"
          aria-label={`${figure.label} (image unavailable)`}
        >
          <ImageOff className="size-5" />
        </span>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- remote /static crop served via rewrite
        <img
          ref={imgRef}
          src={figure.image_url}
          alt={figure.caption ?? figure.label}
          width={w}
          height={h}
          loading="eager"
          onError={() => setBroken(true)}
          className="h-full w-full object-contain"
        />
      )}
    </div>
  );
}

/**
 * Compact horizontal card for a cited figure. Exported so `AnswerBody` can place
 * it inline next to the paragraph that references its `[F#]` marker; the strip
 * below keeps the same card as the fallback when the marker can't be resolved.
 */
export function CitedFigure({
  figure,
  messageId,
}: {
  figure: FigureData;
  messageId: string;
}) {
  const { open } = useEvidenceParam();
  const evidence = useMessageEvidence();
  const marker = figure.marker ?? figure.id;
  const n = figure.marker ? evidence?.numbers.get(figure.marker) : undefined;

  return (
    <div className="flex gap-4 overflow-hidden rounded-lg border border-line bg-surface p-3">
      <button
        type="button"
        onClick={() => open(messageId, marker)}
        aria-label={`Open ${figure.label}`}
        className="shrink-0 overflow-hidden rounded-md border border-line-soft transition-opacity hover:opacity-90"
      >
        <Thumb figure={figure} className="w-40" />
      </button>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium text-ink">
            {figure.label} · p.{figure.page}
          </span>
          {n != null && <NumberBadge n={n} />}
        </div>
        {figure.caption && (
          <p className="mt-1 line-clamp-3 text-xs leading-5 text-ink-soft">
            {figure.caption}
          </p>
        )}
        <button
          type="button"
          onClick={() => open(messageId, marker)}
          className="mt-auto inline-flex w-fit items-center gap-1 pt-2 text-xs font-semibold text-accent-brand hover:underline"
        >
          Open in document
          <ArrowRight className="size-3" />
        </button>
      </div>
    </div>
  );
}

function CandidateThumb({
  figure,
  messageId,
}: {
  figure: FigureData;
  messageId: string;
}) {
  const { open } = useEvidenceParam();
  const marker = figure.marker ?? figure.id;
  return (
    <button
      type="button"
      onClick={() => open(messageId, marker)}
      className="group w-32 overflow-hidden rounded-md border border-line bg-surface text-left transition-colors hover:border-accent-brand/50"
    >
      <Thumb figure={figure} className="w-full border-b border-line-soft" />
      <div className="p-1.5">
        <div className="truncate font-mono text-[0.7rem] text-ink-soft">
          {figure.label}
        </div>
      </div>
    </button>
  );
}

export function FiguresStrip({
  messageId,
  figures,
}: {
  messageId: string;
  figures: FigureData[];
}) {
  if (figures.length === 0) return null;
  const cited = figures.filter((f) => f.display === "cited");
  const candidates = figures.filter((f) => f.display !== "cited");

  return (
    <div className="mt-4 max-w-[560px] space-y-3">
      {cited.length > 0 && (
        <div className="space-y-3">
          {cited.map((f) => (
            <CitedFigure key={f.id} figure={f} messageId={messageId} />
          ))}
        </div>
      )}
      {candidates.length > 0 && (
        <div>
          <div className="pl-mono-label mb-1.5">Related figures</div>
          <div className="flex flex-wrap gap-2">
            {candidates.map((f) => (
              <CandidateThumb key={f.id} figure={f} messageId={messageId} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
