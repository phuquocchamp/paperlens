"use client";

/**
 * Per-answer feedback row (task spec §6): copy, thumbs up, thumbs down as icon
 * buttons. Copy works; the thumbs are non-functional stubs (message_feedback +
 * Langfuse sync land in P4). Rendered under each settled assistant answer.
 */

import { useState } from "react";
import { Check, Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import { cn } from "@/lib/utils";

function IconBtn({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "flex size-7 items-center justify-center rounded-md text-ink-faint transition-colors",
        "hover:bg-accent hover:text-accent-foreground",
        active && "bg-accent text-accent-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function FeedbackRow({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(null);

  const copy = () => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  };

  return (
    <div className="mt-3 flex items-center gap-0.5">
      <IconBtn label="Copy answer" onClick={copy} active={copied}>
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </IconBtn>
      <IconBtn
        label="Good answer"
        active={vote === "up"}
        onClick={() => setVote((v) => (v === "up" ? null : "up"))}
      >
        <ThumbsUp className="size-3.5" />
      </IconBtn>
      <IconBtn
        label="Bad answer"
        active={vote === "down"}
        onClick={() => setVote((v) => (v === "down" ? null : "down"))}
      >
        <ThumbsDown className="size-3.5" />
      </IconBtn>
    </div>
  );
}
