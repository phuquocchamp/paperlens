/**
 * Client-side types + same-origin fetchers for the conversation (chat history)
 * REST surface, plus a pure mapper from stored `messages.parts` to the
 * `PaperLensUIMessage` shape `useChat` reloads from.
 *
 * As with `lib/projects.ts`, the browser NEVER talks to FastAPI directly — it
 * goes through the same-origin BFF routes under `/api/conversations/**`, which
 * forward to `PAPERLENS_API_URL` server-side. Field names mirror the backend
 * `ConversationSummary` / `ConversationDetail` DTOs verbatim (snake_case).
 */

import type { PaperLensUIMessage, PaperLensUIPart } from "@/lib/types";

// --------------------------------------------------------------------------- //
// Types (mirror backend ConversationSummary / ConversationDetail)
// --------------------------------------------------------------------------- //
export interface ConversationSummary {
  id: string;
  title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  message_count: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system" | string;
  /** UIMessage.parts JSONB, stored verbatim by the chat route. */
  parts: unknown[];
  created_at?: string | null;
}

export interface ConversationDetail {
  id: string;
  project_id: string;
  title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  messages: ConversationMessage[];
}

// --------------------------------------------------------------------------- //
// parts -> PaperLensUIMessage (used by the RSC page and any client reload)
// --------------------------------------------------------------------------- //

/**
 * Map one stored message to the `useChat` message shape. `parts` is passed
 * through untouched (data-citation / data-figure / data-table / text parts
 * render identically to the live stream). The DB message id is reused so
 * `?ev=msgId:marker` selections stay stable across reloads.
 */
export function toUIMessage(m: ConversationMessage): PaperLensUIMessage {
  return {
    id: m.id,
    role: (m.role === "assistant" ? "assistant" : "user") as
      | "assistant"
      | "user",
    parts: (m.parts ?? []) as PaperLensUIPart[],
  };
}

export function toInitialMessages(
  detail: ConversationDetail,
): PaperLensUIMessage[] {
  return detail.messages.map(toUIMessage);
}

// --------------------------------------------------------------------------- //
// Same-origin fetchers (browser → BFF)
// --------------------------------------------------------------------------- //
async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function fetchConversations(
  projectId: string,
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  return jsonOrThrow<ConversationSummary[]>(
    await fetch(
      `/api/conversations?projectId=${encodeURIComponent(projectId)}`,
      { signal, cache: "no-store" },
    ),
  );
}

export async function createConversation(input: {
  project_id: string;
  title?: string | null;
}): Promise<ConversationSummary> {
  return jsonOrThrow<ConversationSummary>(
    await fetch("/api/conversations", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function renameConversation(
  id: string,
  title: string | null,
): Promise<ConversationSummary> {
  return jsonOrThrow<ConversationSummary>(
    await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  );
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
}

// --------------------------------------------------------------------------- //
// React Query keys
// --------------------------------------------------------------------------- //
export const conversationKeys = {
  list: (projectId: string) => ["conversations", projectId] as const,
  detail: (id: string) => ["conversation", id] as const,
};
