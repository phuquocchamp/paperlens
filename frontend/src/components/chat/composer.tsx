"use client";

/**
 * Chat composer. The Send button becomes a Stop button while streaming — Stop
 * calls BOTH `useChat().stop()` (aborts the browser fetch) AND the backend stop
 * endpoint (disconnect ≠ cancel — CONTRACT §2). Enter submits; Shift+Enter adds
 * a newline.
 *
 * Enter reliability (task §3): the textarea is NEVER given the `disabled`
 * attribute — a disabled textarea drops keydown and cannot hold focus, so focus
 * escapes to a navigable control (e.g. the sidebar "New chat" link) and a stray
 * Enter navigates instead of submitting. Instead we keep it focusable, autofocus
 * it, dim it visually when gated, and gate SUBMISSION in the handler. Enter
 * always `preventDefault()`s (no newline, no bubbling to a route change) and the
 * IME composition Enter is ignored.
 */

import { useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Composer({
  input,
  onInputChange,
  onSubmit,
  onStop,
  isStreaming,
  disabled,
  disabledReason,
}: {
  input: string;
  onInputChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
  disabledReason?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    if (isStreaming || disabled) return;
    if (!input.trim()) return;
    onSubmit();
  };

  return (
    <div className="bg-paper/80 sticky bottom-0 border-t border-line backdrop-blur">
      <div className="mx-auto w-full max-w-[780px] px-4 py-3">
        <div
          className={cn(
            "flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-sm transition-colors focus-within:border-accent-brand/60",
            disabled && "opacity-60",
          )}
        >
          <textarea
            ref={textareaRef}
            value={input}
            autoFocus
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={(e) => {
              // Enter (without Shift, outside IME composition) submits. Always
              // preventDefault + stopPropagation so a gated/empty Enter can
              // never insert a newline or bubble up into a route change.
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                e.stopPropagation();
                submit();
              }
            }}
            rows={1}
            placeholder={
              disabled
                ? (disabledReason ?? "No documents ready yet")
                : "Ask about your papers…"
            }
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint"
          />
          {isStreaming ? (
            <Button
              type="button"
              size="icon"
              variant="secondary"
              onClick={onStop}
              aria-label="Stop"
              className="shrink-0 rounded-full"
            >
              <Square className="size-4 fill-current" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              onClick={submit}
              disabled={disabled || !input.trim()}
              aria-label="Send"
              className="shrink-0 rounded-full bg-primary text-primary-foreground hover:bg-accent-brand-ink"
            >
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
        <p className="mt-2 text-center text-xs text-ink-faint">
          Answers cite only documents in this project · figures &amp; tables
          included when relevant
        </p>
      </div>
    </div>
  );
}
