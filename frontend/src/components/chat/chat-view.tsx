"use client";

/**
 * ChatView — the streaming Q&A surface (artboards 1 & 2), wired to `ai` v7
 * `useChat` (@ai-sdk/react 4.x, resolved via context7 against `ai@7`).
 *
 * Key protocol wiring (CONTRACT §2/§6):
 *   - Transport posts to the same-origin BFF `/api/chat`. A custom `fetch`
 *     captures the backend's `x-paperlens-stream-id` response header so Stop can
 *     address it — the id is NOT in the SSE frames (see report / reconcile note).
 *   - `onData` handles the transient `data-status` part → local state, cleared
 *     when the run leaves streaming/submitted (data-status never lands in parts).
 *   - Stop calls BOTH `stop()` (abort the fetch) and the backend stop endpoint,
 *     because disconnect ≠ cancel.
 *   - Evidence selection is URL state (`?ev=msgId:marker`), resolved here and
 *     handed to the contextual EvidencePanel.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

import type {
  CitationData,
  FigureData,
  PaperLensUIMessage,
  TableData,
} from "@/lib/types";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useProjects } from "@/hooks/use-projects";
import { useDocuments } from "@/hooks/use-documents";
import { useActiveProject } from "@/components/active-project-provider";
import { isQueryable } from "@/lib/projects";
import { createConversation, conversationKeys } from "@/lib/conversations";
import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { EvidencePanel } from "./evidence-panel";
import { buildMarkerNumbers, type ResolvedEvidence } from "./evidence-context";
import type { AnswerSource } from "./evidence-content";
import { StatusLine } from "./status-line";
import { useEvidenceParam } from "./use-evidence-param";

/** Resolve a `?ev=msgId:marker` selection against the live message list. */
function resolveSelection(
  messages: PaperLensUIMessage[],
  selection: { messageId: string; marker: string } | null,
): ResolvedEvidence | null {
  if (!selection) return null;
  const message = messages.find((m) => m.id === selection.messageId);
  if (!message) return null;
  for (const part of message.parts) {
    if (part.type === "data-citation" && part.data.marker === selection.marker) {
      return { kind: "citation", data: part.data as CitationData };
    }
    if (part.type === "data-figure" && part.data.marker === selection.marker) {
      return { kind: "figure", data: part.data as FigureData };
    }
    if (part.type === "data-table" && part.data.marker === selection.marker) {
      return { kind: "table", data: part.data as TableData };
    }
  }
  // Figures may be addressed by their id when not yet cited (no marker).
  for (const part of message.parts) {
    if (part.type === "data-figure" && part.data.id === selection.marker) {
      return { kind: "figure", data: part.data as FigureData };
    }
  }
  return null;
}

export function ChatView({
  chatId,
  initialMessages,
}: {
  chatId?: string;
  initialMessages?: PaperLensUIMessage[];
}) {
  const [input, setInput] = useState("");
  const [statusDetail, setStatusDetail] = useState<string | null>(null);
  const streamIdRef = useRef<string | null>(null);
  const queryClient = useQueryClient();

  // The conversation this view writes to. For an existing chat it is the route's
  // chatId; for a NEW chat it is null until the first turn pre-creates one (see
  // `submit`), so the id round-trips to `ChatRequest.conversation_id` and both
  // turns append to the same conversation.
  const conversationIdRef = useRef<string | null>(chatId ?? null);
  // Guards against a double pre-create if two sends race before the first
  // resolves.
  const creatingRef = useRef(false);

  // Active project — carried as `project_id` in the chat request body, and used
  // for the breadcrumb + composer gating. Kept in a ref so the stable transport
  // and submit callback always read the current id (never a stale closure).
  const { activeProjectId } = useActiveProject();
  const { data: projects } = useProjects();
  const activeProject = useMemo(
    () =>
      projects?.find((p) => p.id === activeProjectId) ?? projects?.[0] ?? null,
    [projects, activeProjectId],
  );
  const activeProjectIdRef = useRef<string | null>(activeProject?.id ?? null);
  activeProjectIdRef.current = activeProject?.id ?? null;
  const { data: documents } = useDocuments(activeProject?.id ?? null);

  // Stable transport with a fetch wrapper that captures the stream id header.
  const [transport] = useState(
    () =>
      new DefaultChatTransport<PaperLensUIMessage>({
        api: "/api/chat",
        fetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
          const res = await fetch(input, init);
          const id = res.headers.get("x-paperlens-stream-id");
          if (id) streamIdRef.current = id;
          return res;
          // Cast: the SDK FetchFunction type is structurally the global fetch.
        }) as typeof fetch,
      }),
  );

  const { messages, sendMessage, status, stop, setMessages } =
    useChat<PaperLensUIMessage>({
    id: chatId,
    messages: initialMessages,
    transport,
    onData: (dataPart) => {
      // Transient status is delivered ONLY here, never in message.parts.
      if (dataPart.type === "data-status") {
        setStatusDetail(dataPart.data.detail ?? null);
      }
    },
  });

  const isStreaming = status === "streaming" || status === "submitted";

  // Clear the transient status line once the run settles.
  useEffect(() => {
    if (!isStreaming) setStatusDetail(null);
  }, [isStreaming]);

  // When a run settles, the backend has (just) persisted the turn. Re-fetch the
  // sidebar list so a newly-populated conversation appears — the list uses an
  // INNER JOIN, so an empty pre-created conversation is invisible until now.
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    const settled = wasStreamingRef.current && !isStreaming;
    wasStreamingRef.current = isStreaming;
    if (!settled) return;
    const projectId = activeProjectIdRef.current;
    if (!projectId) return;
    const invalidate = () =>
      queryClient.invalidateQueries({
        queryKey: conversationKeys.list(projectId),
      });
    // Immediately, then once more shortly after — persistence is a shielded task
    // that commits just after the stream settles, so the first refetch can race
    // it.
    void invalidate();
    const t = setTimeout(() => void invalidate(), 900);
    return () => clearTimeout(t);
  }, [isStreaming, queryClient]);

  const submit = useCallback(async () => {
    const text = input.trim();
    if (!text) return;
    streamIdRef.current = null;
    setInput("");

    const projectId = activeProjectIdRef.current;

    // NEW chat, first turn: pre-create a conversation so we learn its id up
    // front (the chat route never returns it). Best-effort — if it fails we send
    // with conversation_id=null and the backend still persists under its own new
    // conversation; the user just gets the sidebar entry after a refresh.
    if (!conversationIdRef.current && projectId && !creatingRef.current) {
      creatingRef.current = true;
      try {
        const conv = await createConversation({
          project_id: projectId,
          title: text,
        });
        conversationIdRef.current = conv.id;
        // NOTE: the URL is intentionally NOT changed here. Switching to
        // /chat/[chatId] mid-stream remounts this view (Next re-runs the RSC
        // segment), which aborts the live stream and blanks the answer. The URL
        // is updated once the run settles (see the settle effect below).
        void queryClient.invalidateQueries({
          queryKey: conversationKeys.list(projectId),
        });
      } catch {
        /* graceful: fall through with a null conversation id */
      } finally {
        creatingRef.current = false;
      }
    }

    // Carry project + conversation id in the request body (ChatRequest).
    sendMessage(
      { text },
      {
        body: {
          project_id: projectId,
          conversation_id: conversationIdRef.current,
        },
      },
    );
  }, [input, sendMessage, queryClient]);

  const handleStop = useCallback(() => {
    // 1) Abort the browser fetch.
    void stop();
    // 2) Actually stop backend generation (disconnect ≠ cancel). Fire-and-forget.
    const id = streamIdRef.current;
    if (id) {
      void fetch(`/api/chat/streams/${encodeURIComponent(id)}/stop`, {
        method: "POST",
      }).catch(() => {
        /* Stop must never surface an error. */
      });
    }
  }, [stop]);

  const { selection, open } = useEvidenceParam();
  const selectedEvidence = useMemo(
    () => resolveSelection(messages, selection),
    [messages, selection],
  );

  // The full numbered source list for the selected answer (panel §8:
  // "ALL SOURCES IN THIS ANSWER") + the active number, all from the shared
  // marker→number map so the panel agrees with the in-text badges.
  const { answerSources, activeNumber } = useMemo(() => {
    if (!selection) return { answerSources: [] as AnswerSource[], activeNumber: undefined };
    const message = messages.find((m) => m.id === selection.messageId);
    if (!message) return { answerSources: [] as AnswerSource[], activeNumber: undefined };
    const numbers = buildMarkerNumbers(message);
    const sources: AnswerSource[] = [];
    for (const part of message.parts) {
      if (part.type === "data-citation") {
        const n = numbers.get(part.data.marker);
        if (n != null) {
          sources.push({
            marker: part.data.marker,
            n,
            filename: part.data.filename,
            page: part.data.page,
          });
        }
      }
    }
    sources.sort((a, b) => a.n - b.n);
    return { answerSources: sources, activeNumber: numbers.get(selection.marker) };
  }, [messages, selection]);

  // Scope chip is wired to the ?docs=id1,id2 param (CONTRACT §6 state map); it
  // falls back to the count of QUERYABLE documents in the active project (the
  // same documents query the sidebar uses) when the scope is unset. `undefined`
  // while the documents query is loading, so the chip shows an em-dash rather
  // than flashing a wrong "0 docs".
  const searchParams = useSearchParams();

  // "New chat" from the sidebar pushes /chat?new=<ts>. Because a same-URL
  // navigation does NOT remount this view, that changing token is our signal to
  // reset the in-memory session — otherwise the previous conversation's messages
  // and id would linger and a "new" chat would silently append to it. Only on
  // the new-chat route (no chatId); existing chats keep their loaded history.
  const newSessionToken = searchParams.get("new");
  useEffect(() => {
    if (chatId) return;
    conversationIdRef.current = null;
    creatingRef.current = false;
    setMessages([]);
    setInput("");
    setStatusDetail(null);
    // Only re-run when the token changes (a fresh "New chat" click).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newSessionToken, chatId]);

  const docsParam = searchParams.get("docs");
  const scopeCount: number | undefined = docsParam
    ? docsParam.split(",").filter(Boolean).length
    : documents
      ? documents.filter((d) => isQueryable(d.status)).length
      : undefined;
  const scopeLabel =
    scopeCount == null
      ? "scope: —"
      : `scope: ${scopeCount} ${scopeCount === 1 ? "doc" : "docs"}`;
  // Model is static for P0 (the backend `start` frame carries the real value).
  const modelName = "deepseek-chat";
  // Breadcrumb title: the first user turn (matches the stored conversation
  // title), else "New chat" for an empty view.
  const chatTitle = useMemo(() => {
    const firstUser = messages.find((m) => m.role === "user");
    if (firstUser) {
      const text = firstUser.parts
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("")
        .trim();
      if (text) return text.length > 80 ? `${text.slice(0, 80)}…` : text;
    }
    return "New chat";
  }, [messages]);
  const projectName = activeProject?.name ?? "No project";

  // Composer gating. It is disabled ONLY once the documents query has resolved
  // and none are queryable — NOT while the query is still loading. Disabling
  // during the load window would drop the textarea's focus/keydown, letting a
  // stray Enter escape to a navigable control instead of submitting (task §3).
  const composerDisabled = useMemo(
    () => documents !== undefined && !documents.some((d) => isQueryable(d.status)),
    [documents],
  );

  const lastAssistantId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  }, [messages]);

  return (
    <div className="flex h-svh min-h-0 w-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
          <SidebarTrigger />
          {/* Breadcrumb: Project / Chat title */}
          <nav className="flex min-w-0 items-center gap-1.5 text-sm">
            <span className="truncate text-ink-soft">{projectName}</span>
            <ChevronRight className="size-3.5 shrink-0 text-ink-faint" />
            <span className="truncate font-medium text-ink">{chatTitle}</span>
          </nav>
          {/* Mono chips: retrieval scope + model */}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <span className="rounded-md border border-line bg-surface px-2 py-1 font-mono text-[0.7rem] text-ink-soft">
              {scopeLabel}
            </span>
            <span className="rounded-md border border-line bg-surface px-2 py-1 font-mono text-[0.7rem] text-ink-soft">
              {modelName}
            </span>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[780px] px-4">
            {messages.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center py-24 text-center">
                <p className="font-heading text-lg font-semibold text-ink">
                  Ask about your papers
                </p>
                <p className="mt-1 max-w-sm text-sm text-ink-faint">
                  Every answer shows its sources first, with page-verifiable
                  citations you can open.
                </p>
              </div>
            )}

            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end py-4">
                  <div className="max-w-[80%] rounded-2xl bg-ink px-4 py-2 text-sm text-paper">
                    {message.parts.map((p, i) =>
                      p.type === "text" ? <span key={i}>{p.text}</span> : null,
                    )}
                  </div>
                </div>
              ) : (
                <AssistantMessage
                  key={message.id}
                  message={message}
                  isStreaming={isStreaming && message.id === lastAssistantId}
                />
              ),
            )}

            {status === "submitted" && <StatusLine detail={statusDetail} />}
            {status === "streaming" && statusDetail && (
              <StatusLine detail={statusDetail} />
            )}
          </div>
        </div>

        <Composer
          input={input}
          onInputChange={setInput}
          onSubmit={submit}
          onStop={handleStop}
          isStreaming={isStreaming}
          disabled={composerDisabled}
          disabledReason="Upload a document to start asking"
        />
      </div>

      <EvidencePanel
        evidence={selectedEvidence}
        sources={answerSources}
        activeNumber={activeNumber}
        onSelectSource={(marker) =>
          selection && open(selection.messageId, marker)
        }
      />
    </div>
  );
}
