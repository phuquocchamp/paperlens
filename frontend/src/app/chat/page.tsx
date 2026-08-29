import { Suspense } from "react";
import { ChatView } from "@/components/chat/chat-view";

/**
 * New-chat route. ChatView reads `?ev` via useSearchParams, so it must sit
 * inside a Suspense boundary (Next 15 prerender requirement).
 */
export default function NewChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatView />
    </Suspense>
  );
}
