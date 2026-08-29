"use client";

/**
 * EvidenceContent — the body of the evidence panel (artboard 3), deliberately
 * separated from its container (CONTRACT §6) so the same content renders inside a
 * right-side panel (≥ md) or a bottom Sheet (< md).
 *
 * Layout: document filename + section·page in mono → the quote with a soft
 * highlight → the figure (for figure evidence) → a green "Open PDF at p.N"
 * button + a "Copy quote" button → an "ALL SOURCES IN THIS ANSWER" mono-labelled
 * list of the numbered sources. The quote-highlight alignment span lands in P3;
 * P0 highlights the whole dereferenced quote.
 */

import { useState } from "react";
import { Check, Copy, ExternalLink, ImageOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FigureData } from "@/lib/types";
import type { ResolvedEvidence } from "./evidence-context";
import { NumberBadge } from "./number-badge";
import { TableBody } from "./table-body";
import { VerifyBadge } from "./verify-badge";

/** Figure image that degrades to a neutral placeholder instead of a broken icon. */
function EvidenceFigureImage({ figure }: { figure: FigureData }) {
  const [broken, setBroken] = useState(false);
  const w = figure.width ?? undefined;
  const h = figure.height ?? undefined;
  if (broken) {
    return (
      <div
        className="flex aspect-[16/9] w-full flex-col items-center justify-center gap-1 text-ink-faint"
        role="img"
        aria-label={`${figure.label} (image unavailable)`}
      >
        <ImageOff className="size-6" />
        <span className="font-mono text-[0.7rem]">Image unavailable</span>
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- remote /static crop served via rewrite
    <img
      src={figure.image_url}
      alt={figure.caption ?? figure.label}
      width={w}
      height={h}
      onError={() => setBroken(true)}
      className="w-full object-contain"
    />
  );
}

/** One numbered source shown in the "ALL SOURCES IN THIS ANSWER" list. */
export interface AnswerSource {
  marker: string;
  n: number;
  filename: string;
  page: number;
}

function AllSources({
  sources,
  activeNumber,
  onSelect,
}: {
  sources: AnswerSource[];
  activeNumber?: number;
  onSelect?: (marker: string) => void;
}) {
  if (sources.length === 0) return null;
  return (
    <div className="border-t border-line pt-4">
      <div className="pl-mono-label mb-2">All sources in this answer</div>
      <ul className="space-y-1">
        {sources.map((s) => (
          <li key={s.marker}>
            <button
              type="button"
              onClick={() => onSelect?.(s.marker)}
              className={
                "flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left transition-colors " +
                (s.n === activeNumber
                  ? "border-accent-brand/40 bg-accent-brand-soft"
                  : "border-transparent hover:bg-line-soft")
              }
            >
              <NumberBadge n={s.n} />
              <span className="truncate font-mono text-xs text-ink-soft">
                {s.filename} · p.{s.page}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CopyQuoteButton({ quote }: { quote: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      className="border-line"
      onClick={() =>
        void navigator.clipboard?.writeText(quote).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        })
      }
    >
      {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      {copied ? "Copied" : "Copy quote"}
    </Button>
  );
}

export function EvidenceContent({
  evidence,
  sources = [],
  activeNumber,
  onSelectSource,
}: {
  evidence: ResolvedEvidence;
  sources?: AnswerSource[];
  activeNumber?: number;
  onSelectSource?: (marker: string) => void;
}) {
  if (evidence.kind === "citation") {
    const c = evidence.data;
    return (
      <div className="space-y-4">
        <header className="space-y-1">
          <div className="text-sm font-medium text-ink">{c.filename}</div>
          <div className="font-mono text-xs text-ink-faint">
            {c.section_path ? `${c.section_path} · ` : ""}page {c.page}
          </div>
          <VerifyBadge status={c.verify_status} className="mt-1" />
        </header>

        {/* Quote with a soft highlight (full quote in P0; aligned span in P3). */}
        <blockquote className="rounded-md border-l-2 border-accent-brand/50 bg-accent-brand-soft py-2 pl-3 pr-2 text-sm leading-6 text-ink">
          <mark className="bg-transparent text-ink">“{c.quote}”</mark>
        </blockquote>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            className="bg-primary text-primary-foreground hover:bg-accent-brand-ink"
            disabled
          >
            <ExternalLink className="size-4" />
            Open PDF at p.{c.page}
          </Button>
          <CopyQuoteButton quote={c.quote} />
        </div>

        <AllSources
          sources={sources}
          activeNumber={activeNumber}
          onSelect={onSelectSource}
        />
      </div>
    );
  }

  if (evidence.kind === "figure") {
    const f = evidence.data;
    return (
      <div className="space-y-4">
        <header className="space-y-1">
          <div className="text-sm font-medium text-ink">{f.label}</div>
          <div className="font-mono text-xs text-ink-faint">page {f.page}</div>
        </header>
        <div className="overflow-hidden rounded-md border border-line bg-line-soft">
          <EvidenceFigureImage figure={f} />
        </div>
        {f.caption && (
          <p className="font-mono text-xs text-ink-soft">
            {f.label} — {f.caption}
          </p>
        )}
        <Button
          size="sm"
          className="bg-primary text-primary-foreground hover:bg-accent-brand-ink"
          disabled
        >
          <ExternalLink className="size-4" />
          Open PDF at p.{f.page}
        </Button>

        <AllSources
          sources={sources}
          activeNumber={activeNumber}
          onSelect={onSelectSource}
        />
      </div>
    );
  }

  const t = evidence.data;
  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <div className="text-sm font-medium text-ink">{t.label}</div>
        <div className="font-mono text-xs text-ink-faint">page {t.page}</div>
      </header>
      <div className="overflow-x-auto rounded-md border border-line text-sm">
        <TableBody table={t} />
      </div>

      <AllSources
        sources={sources}
        activeNumber={activeNumber}
        onSelect={onSelectSource}
      />
    </div>
  );
}
