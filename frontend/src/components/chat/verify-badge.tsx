"use client";

/**
 * Verify-status badge for a citation. The status is the ONE source of truth from
 * the second `data-citation` emission (pending → grounded | weak | unverifiable
 * | source_changed) per CONTRACT §2. During P0 the fake stream flips the first
 * two citations to grounded/weak so the transition is visible.
 */

import { CheckCircle2, CircleAlert, CircleHelp, Loader2, RefreshCcw } from "lucide-react";
import type { VerifyStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const CONFIG: Record<
  VerifyStatus,
  { label: string; className: string; icon: React.ElementType; spin?: boolean }
> = {
  pending: {
    label: "Verifying",
    className: "text-ink-faint border-line",
    icon: Loader2,
    spin: true,
  },
  grounded: {
    label: "Grounded",
    className: "text-accent-brand-ink border-accent-brand/30 bg-accent-brand-soft",
    icon: CheckCircle2,
  },
  weak: {
    label: "Weak match",
    className: "text-amber border-amber/30 bg-amber-soft",
    icon: CircleAlert,
  },
  unverifiable: {
    label: "Unverifiable",
    className: "text-red border-red/30 bg-red-soft",
    icon: CircleHelp,
  },
  source_changed: {
    label: "Source changed",
    className: "text-amber border-amber/30 bg-amber-soft",
    icon: RefreshCcw,
  },
};

export function VerifyBadge({
  status,
  className,
}: {
  status: VerifyStatus;
  className?: string;
}) {
  const cfg = CONFIG[status];
  const Icon = cfg.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        cfg.className,
        className,
      )}
    >
      <Icon className={cn("size-3", cfg.spin && "animate-spin")} />
      {cfg.label}
    </span>
  );
}
