"use client";

/**
 * React Query hooks for a project's saved conversations (sidebar chat history).
 *
 * The list is the source of truth for the "Chats" section. After a new chat's
 * first turn persists, the chat view invalidates `conversationKeys.list` so the
 * new entry appears without a manual refresh.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  conversationKeys,
  deleteConversation,
  fetchConversations,
  type ConversationSummary,
} from "@/lib/conversations";

export function useConversations(projectId: string | null) {
  return useQuery({
    queryKey: projectId
      ? conversationKeys.list(projectId)
      : ["conversations", "none"],
    queryFn: ({ signal }) => fetchConversations(projectId as string, signal),
    enabled: !!projectId,
  });
}

export function useDeleteConversation(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onMutate: async (id: string) => {
      if (!projectId) return;
      await qc.cancelQueries({ queryKey: conversationKeys.list(projectId) });
      qc.setQueryData<ConversationSummary[]>(
        conversationKeys.list(projectId),
        (prev) => (prev ?? []).filter((c) => c.id !== id),
      );
    },
    onSettled: () => {
      if (!projectId) return;
      void qc.invalidateQueries({ queryKey: conversationKeys.list(projectId) });
    },
  });
}
