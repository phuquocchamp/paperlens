"use client";

/**
 * React Query hooks for a project's documents, with dynamic ingest polling.
 *
 * Polling cadence (CONTRACT §6 spirit): while any document is still moving,
 * poll on a tier that widens with the youngest active job's age
 *   1s → 2.5s → 5s → 10s
 * so a freshly-uploaded doc updates snappily while long-tail captioning backs
 * off. `refetchIntervalInBackground` is false — no polling on a hidden tab.
 *
 * Terminal set (stop polling once ALL docs are here): full_ready, rejected,
 * failed, deleted. `text_ready` is deliberately kept on the slowest 10s tier —
 * captioning continues toward full_ready, so figure_count is still climbing.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchDocuments,
  isTerminal,
  queryKeys,
  uploadDocument,
  type DocumentRow,
} from "@/lib/projects";

// --------------------------------------------------------------------------- //
// Same-origin fetchers for the per-document actions (M8). Kept inline (not in
// lib/projects.ts) so this hook owns the delete/retry wiring end to end. Both
// go through the same-origin BFF proxy under /api/*.
// --------------------------------------------------------------------------- //

/** DELETE a document. 204/other-empty bodies are tolerated (no res.json()). */
async function deleteDocumentReq(
  projectId: string,
  documentId: string,
): Promise<void> {
  const res = await fetch(
    `/api/projects/${projectId}/documents/${documentId}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : JSON.stringify(body?.detail ?? body);
    } catch {
      /* empty / non-JSON body (e.g. 204) */
    }
    throw new Error(
      `Delete failed (${res.status})${detail ? `: ${detail}` : ""}`,
    );
  }
}

/** POST retry for a FAILED document; returns the updated row on success. */
async function retryDocumentReq(
  projectId: string,
  documentId: string,
): Promise<DocumentRow> {
  const res = await fetch(
    `/api/projects/${projectId}/documents/${documentId}/retry`,
    { method: "POST" },
  );
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : JSON.stringify(body?.detail ?? body);
    } catch {
      /* non-JSON body */
    }
    throw new Error(
      `Retry failed (${res.status})${detail ? `: ${detail}` : ""}`,
    );
  }
  return res.json() as Promise<DocumentRow>;
}

/** Youngest still-active doc's age → poll interval (ms). */
function pollIntervalFor(docs: DocumentRow[]): number | false {
  const active = docs.filter((d) => !isTerminal(d.status));
  if (active.length === 0) return false; // everything settled — stop polling

  // text_ready docs are "active" (captioning continues) but slow-moving.
  const onlyTextReady = active.every((d) => d.status === "text_ready");
  if (onlyTextReady) return 10_000;

  // Otherwise tier by the youngest active job's age.
  const now = Date.now();
  let youngestAgeMs = Number.POSITIVE_INFINITY;
  for (const d of active) {
    const created = d.created_at ? Date.parse(d.created_at) : NaN;
    if (!Number.isNaN(created)) {
      youngestAgeMs = Math.min(youngestAgeMs, now - created);
    }
  }
  if (!Number.isFinite(youngestAgeMs)) return 2_500;
  if (youngestAgeMs < 15_000) return 1_000;
  if (youngestAgeMs < 45_000) return 2_500;
  if (youngestAgeMs < 120_000) return 5_000;
  return 10_000;
}

export function useDocuments(projectId: string | null) {
  return useQuery({
    queryKey: projectId ? queryKeys.documents(projectId) : ["documents", "none"],
    queryFn: ({ signal }) => fetchDocuments(projectId as string, signal),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const data = query.state.data as DocumentRow[] | undefined;
      if (!data) return false;
      return pollIntervalFor(data);
    },
    refetchIntervalInBackground: false,
  });
}

/** Build an optimistic placeholder row shown the instant a file is dropped. */
function optimisticRow(projectId: string, file: File): DocumentRow {
  return {
    id: `optimistic-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    project_id: projectId,
    title: null,
    filename: file.name,
    page_count: null,
    status: "uploaded",
    stage: null,
    progress: null,
    error_code: null,
    error_message: null,
    chunk_count: 0,
    figure_count: 0,
    created_at: new Date().toISOString(),
  };
}

export function useUploadDocument(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      if (!projectId) throw new Error("No active project");
      return uploadDocument(projectId, file);
    },
    // Show a row immediately (optimistic), keyed so onSuccess can replace it.
    onMutate: async (file: File) => {
      if (!projectId) return { optimisticId: null as string | null };
      await qc.cancelQueries({ queryKey: queryKeys.documents(projectId) });
      const row = optimisticRow(projectId, file);
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) =>
        prev ? [row, ...prev] : [row],
      );
      return { optimisticId: row.id };
    },
    onError: (_err, _file, context) => {
      if (!projectId || !context?.optimisticId) return;
      // Drop the optimistic row — the upload never landed.
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) =>
        (prev ?? []).filter((d) => d.id !== context.optimisticId),
      );
    },
    onSuccess: (doc: DocumentRow, _file, context) => {
      if (!projectId) return;
      // Swap the optimistic row for the real one.
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) => {
        if (!prev) return [doc];
        const filtered = prev.filter((d) => d.id !== context?.optimisticId);
        return [doc, ...filtered.filter((d) => d.id !== doc.id)];
      });
    },
    onSettled: () => {
      if (!projectId) return;
      void qc.invalidateQueries({ queryKey: queryKeys.documents(projectId) });
    },
  });
}

/** Delete a document, optimistically removing its row from the table. */
export function useDeleteDocument(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => {
      if (!projectId) throw new Error("No active project");
      return deleteDocumentReq(projectId, documentId);
    },
    onMutate: async (documentId: string) => {
      if (!projectId) return { previous: undefined };
      // Cancel in-flight polls so a late refetch can't flash the row back.
      await qc.cancelQueries({ queryKey: queryKeys.documents(projectId) });
      const previous = qc.getQueryData<DocumentRow[]>(
        queryKeys.documents(projectId),
      );
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) =>
        (prev ?? []).filter((d) => d.id !== documentId),
      );
      return { previous };
    },
    onError: (_err, _documentId, context) => {
      if (!projectId || !context?.previous) return;
      // Restore the table on failure.
      qc.setQueryData<DocumentRow[]>(
        queryKeys.documents(projectId),
        context.previous,
      );
    },
    onSettled: () => {
      if (!projectId) return;
      void qc.invalidateQueries({ queryKey: queryKeys.documents(projectId) });
    },
  });
}

/** Retry a FAILED document; the row returns to Queued and polling resumes. */
export function useRetryDocument(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => {
      if (!projectId) throw new Error("No active project");
      return retryDocumentReq(projectId, documentId);
    },
    onMutate: async (documentId: string) => {
      if (!projectId) return;
      await qc.cancelQueries({ queryKey: queryKeys.documents(projectId) });
      // Optimistically flip the row to queued so polling wakes up immediately.
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) =>
        (prev ?? []).map((d) =>
          d.id === documentId
            ? {
                ...d,
                status: "queued",
                stage: null,
                progress: null,
                error_code: null,
                error_message: null,
              }
            : d,
        ),
      );
    },
    onSuccess: (doc: DocumentRow) => {
      if (!projectId) return;
      qc.setQueryData<DocumentRow[]>(queryKeys.documents(projectId), (prev) =>
        (prev ?? []).map((d) => (d.id === doc.id ? doc : d)),
      );
    },
    onSettled: () => {
      if (!projectId) return;
      void qc.invalidateQueries({ queryKey: queryKeys.documents(projectId) });
    },
  });
}
