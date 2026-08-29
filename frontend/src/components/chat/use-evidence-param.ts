"use client";

/**
 * Evidence panel selection as URL state: `?ev=msgId:marker` (CONTRACT §6).
 *
 * URL-backed so the open panel is shareable, and X/Esc/Back all close it
 * (CONTRACT §6 / task spec). To make Back close the panel, the FIRST open from a
 * closed state uses `router.push` (adds one history entry, so Back pops `?ev`);
 * switching between markers while already open uses `router.replace` (no history
 * spam); X/Esc close with `replace`.
 */

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export interface EvidenceSelection {
  messageId: string;
  marker: string;
}

export function useEvidenceParam() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const raw = searchParams.get("ev");
  let selection: EvidenceSelection | null = null;
  if (raw) {
    const idx = raw.indexOf(":");
    if (idx > 0) {
      selection = {
        messageId: raw.slice(0, idx),
        marker: raw.slice(idx + 1),
      };
    }
  }

  const buildHref = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next) params.set("ev", next);
      else params.delete("ev");
      const qs = params.toString();
      return qs ? `${pathname}?${qs}` : pathname;
    },
    [searchParams, pathname],
  );

  const isOpen = raw != null;
  const open = useCallback(
    (messageId: string, marker: string) => {
      const href = buildHref(`${messageId}:${marker}`);
      // push on first open (so Back closes); replace when switching markers.
      if (isOpen) router.replace(href, { scroll: false });
      else router.push(href, { scroll: false });
    },
    [router, buildHref, isOpen],
  );

  const close = useCallback(() => {
    router.replace(buildHref(null), { scroll: false });
  }, [router, buildHref]);

  return { selection, open, close };
}
