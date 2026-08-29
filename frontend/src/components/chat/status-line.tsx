"use client";

/**
 * Transient streaming status (artboard 2), e.g. "Searching 4 documents…".
 *
 * The value comes from the transient `data-status` part delivered via `onData`
 * and held in local state — it is NOT part of message.parts and is cleared when
 * status leaves "streaming"/"submitted" (CONTRACT §2: data-status is the only
 * transient part).
 */

export function StatusLine({ detail }: { detail: string | null }) {
  if (!detail) return null;
  return (
    <div className="flex items-center gap-2 py-3 text-sm text-ink-soft">
      <span className="relative flex size-2">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-brand opacity-70" />
        <span className="relative inline-flex size-2 rounded-full bg-accent-brand" />
      </span>
      <span>{detail}</span>
    </div>
  );
}

/** Blinking caret appended to streaming text. */
export function TokenCursor() {
  return (
    <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-accent-brand align-baseline" />
  );
}
