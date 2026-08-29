/**
 * The numbered citation badge (task spec §4): a small rounded green square with
 * a white sequential number. Used for the in-text chip, the sources row, the
 * figure card, and the evidence panel so every surface shows the same number.
 */

import { cn } from "@/lib/utils";

export function NumberBadge({
  n,
  className,
  as = "span",
}: {
  n: number;
  className?: string;
  as?: "span" | "div";
}) {
  const Tag = as;
  return (
    <Tag
      className={cn(
        "inline-flex size-[1.15rem] shrink-0 items-center justify-center rounded-[0.3rem]",
        "bg-primary text-[0.7rem] font-semibold leading-none text-primary-foreground",
        "font-mono tabular-nums",
        className,
      )}
    >
      {n}
    </Tag>
  );
}
