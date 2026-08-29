import { Suspense } from "react";
import { ChatView } from "@/components/chat/chat-view";
import { PAPERLENS_API_URL } from "@/lib/api";
import { toInitialMessages, type ConversationDetail } from "@/lib/conversations";
import type { PaperLensUIMessage } from "@/lib/types";

/**
 * Existing-chat route (NOT an optional catch-all — CONTRACT §6). The server
 * component prefetches the conversation from the backend and passes its stored
 * messages as `initialMessages` (RSC → useChat `messages`), reading
 * `messages.parts` verbatim so the full history + citations render on reload.
 *
 * The chatId IS the conversation id (the client pre-creates the conversation and
 * navigates here). A missing/deleted conversation degrades to an empty chat that
 * still works as a fresh conversation.
 */
async function loadInitialMessages(
  chatId: string,
): Promise<PaperLensUIMessage[] | undefined> {
  try {
    const res = await fetch(
      `${PAPERLENS_API_URL}/v1/conversations/${encodeURIComponent(chatId)}`,
      { headers: { accept: "application/json" }, cache: "no-store" },
    );
    if (!res.ok) return undefined;
    const detail = (await res.json()) as ConversationDetail;
    const messages = toInitialMessages(detail);
    return messages.length ? messages : undefined;
  } catch {
    // Backend unreachable — render a usable (empty) chat rather than error.
    return undefined;
  }
}

export default async function ChatPage({
  params,
}: {
  params: Promise<{ chatId: string }>;
}) {
  const { chatId } = await params; // Next 15: params is async
  const initialMessages = await loadInitialMessages(chatId);

  return (
    <Suspense fallback={null}>
      <ChatView chatId={chatId} initialMessages={initialMessages} />
    </Suspense>
  );
}
