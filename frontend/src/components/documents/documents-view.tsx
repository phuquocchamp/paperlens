"use client";

/**
 * Documents screen (artboard 4) — wired to the REAL backend.
 *
 * A dropzone card POSTs each file to /api/projects/{active}/documents
 * (multipart), showing an optimistic row instantly. The table below is the live
 * `useDocuments` query (dynamic polling) rendering real PAGES / CHUNKS / FIGURES
 * / STATUS. Backend status+stage map onto the existing badge styles:
 *   text_ready|full_ready → "Ready"        (green)
 *   processing(embedding) → "Embedding N%" (amber + progress bar)
 *   processing(other)     → "Parsing…" etc (amber, stage text)
 *   uploaded|queued       → "Queued"        (amber)
 *   rejected              → error badge      (red, e.g. "No text layer")
 *   failed                → "Failed"         (red)
 *
 * Per-row actions (M8): every row has a Delete action (trash icon) guarded by a
 * confirm dialog — it purges vectors/rows/files and soft-deletes the document,
 * removing the row optimistically. `failed` rows additionally get a Retry
 * button (state machine: FAILED -> QUEUED) that re-enqueues ingestion; the row
 * returns to Queued and polling resumes. `rejected` is terminal-no-retry, so it
 * gets Delete only (the backend returns 409 for a retry on a rejected doc).
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { ChevronRight, RotateCw, Trash2, Upload } from "lucide-react";

import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useProjects } from "@/hooks/use-projects";
import {
  useDeleteDocument,
  useDocuments,
  useRetryDocument,
  useUploadDocument,
} from "@/hooks/use-documents";
import { useActiveProject } from "@/components/active-project-provider";
import { isQueryable, type DocumentRow } from "@/lib/projects";

const STAGE_LABEL: Record<string, string> = {
  parsing: "Parsing…",
  cropping: "Cropping…",
  captioning: "Captioning…",
  chunking: "Chunking…",
  embedding: "Embedding",
  indexing: "Indexing…",
};

function StatusBadge({ row }: { row: DocumentRow }) {
  const status = row.status;

  if (status === "text_ready" || status === "full_ready") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-brand-soft px-2.5 py-1 text-xs font-medium text-accent-brand-ink">
        <span className="size-1.5 rounded-full bg-accent-brand" />
        {status === "full_ready" ? "Ready" : "Ready · captioning"}
      </span>
    );
  }

  if (status === "processing") {
    const stage = row.stage ?? "parsing";
    const percent = typeof row.progress === "number" ? row.progress : null;

    if (stage === "embedding" && percent != null) {
      return (
        <div className="w-36">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-soft px-2.5 py-1 text-xs font-medium text-amber">
            <span className="size-1.5 animate-pulse rounded-full bg-amber" />
            Embedding {percent}%
          </span>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line-soft">
            <div
              className="h-full rounded-full bg-amber transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      );
    }

    return (
      <div className="w-36">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-soft px-2.5 py-1 text-xs font-medium text-amber">
          <span className="size-1.5 animate-pulse rounded-full bg-amber" />
          {STAGE_LABEL[stage] ?? "Processing…"}
          {percent != null ? ` ${percent}%` : ""}
        </span>
        {percent != null && (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line-soft">
            <div
              className="h-full rounded-full bg-amber transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}
      </div>
    );
  }

  if (status === "uploaded" || status === "queued") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-soft px-2.5 py-1 text-xs font-medium text-amber">
        <span className="size-1.5 animate-pulse rounded-full bg-amber" />
        Queued
      </span>
    );
  }

  if (status === "rejected" || status === "failed") {
    const noText = row.error_code === "PDF_NO_TEXT_LAYER";
    const label = noText
      ? "No text layer"
      : status === "failed"
        ? "Failed"
        : "Rejected";
    return (
      <span
        title={row.error_message ?? row.error_code ?? undefined}
        className="inline-flex items-center gap-1.5 rounded-full bg-red-soft px-2.5 py-1 text-xs font-medium text-red"
      >
        <span className="size-1.5 rounded-full bg-red" />
        {label}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-line-soft px-2.5 py-1 text-xs font-medium text-ink-soft">
      {status}
    </span>
  );
}

/** Right-aligned numeric cell (em-dash while unknown / zero-before-ready). */
function Num({ value }: { value: number | null | undefined }) {
  return (
    <td className="px-3 py-3 text-right font-mono text-sm tabular-nums text-ink-soft">
      {value == null || value === 0 ? "—" : value}
    </td>
  );
}

export function DocumentsView() {
  const { activeProjectId } = useActiveProject();
  const { data: projects } = useProjects();
  const activeProject = useMemo(
    () =>
      projects?.find((p) => p.id === activeProjectId) ?? projects?.[0] ?? null,
    [projects, activeProjectId],
  );
  const projectId = activeProject?.id ?? null;

  const { data: documents, isLoading } = useDocuments(projectId);
  const upload = useUploadDocument(projectId);
  const del = useDeleteDocument(projectId);
  const retry = useRetryDocument(projectId);

  const [dragging, setDragging] = useState(false);
  // The document currently pending delete-confirmation (null = dialog closed).
  const [pendingDelete, setPendingDelete] = useState<DocumentRow | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const confirmDelete = useCallback(() => {
    if (!pendingDelete) return;
    del.mutate(pendingDelete.id);
    setPendingDelete(null);
  }, [pendingDelete, del]);

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      if (!projectId) return;
      for (const file of Array.from(files)) {
        if (file.type && file.type !== "application/pdf") continue;
        upload.mutate(file);
      }
    },
    [projectId, upload],
  );

  const rows = useMemo(
    () => (documents ?? []).filter((d) => d.status !== "deleted"),
    [documents],
  );

  const readyCount = rows.filter((r) => isQueryable(r.status)).length;
  const processingCount = rows.filter((r) =>
    ["uploaded", "queued", "processing"].includes(r.status),
  ).length;

  return (
    <div className="flex h-svh min-h-0 w-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <SidebarTrigger />
        <nav className="flex min-w-0 items-center gap-1.5 text-sm">
          <span className="truncate text-ink-soft">
            {activeProject?.name ?? "No project"}
          </span>
          <ChevronRight className="size-3.5 shrink-0 text-ink-faint" />
          <span className="truncate font-medium text-ink">Documents</span>
        </nav>
        <span className="ml-auto font-mono text-xs text-ink-faint">
          {readyCount} ready · {processingCount} processing
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 py-6">
          {!projectId ? (
            <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-12 text-center text-sm text-ink-faint">
              Select or create a project to upload documents.
            </div>
          ) : (
            <>
              {/* Dropzone card */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ")
                    inputRef.current?.click();
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
                }}
                className={cn(
                  "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed bg-surface px-6 py-10 text-center transition-colors",
                  dragging
                    ? "border-accent-brand bg-accent-brand-soft"
                    : "border-line hover:border-accent-brand/50 hover:bg-line-soft/40",
                )}
              >
                <Upload className="size-6 text-ink-faint" />
                <div className="text-sm text-ink">
                  <span className="font-medium">Drop PDFs here</span> or{" "}
                  <span className="font-medium text-accent-brand">browse</span>
                </div>
                <div className="text-xs text-ink-faint">
                  PDF only · max 50 MB · figures &amp; tables extracted
                  automatically
                </div>
              </div>
              {/* The file input is a SIBLING of the clickable dropzone, not a
                  child: as a child, input.click() bubbles back to the div's
                  onClick and re-opens the picker in an infinite loop. */}
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) addFiles(e.target.files);
                  e.target.value = "";
                }}
              />

              {upload.isError && (
                <p className="mt-3 text-sm text-red">
                  Upload failed: {(upload.error as Error).message}
                </p>
              )}
              {del.isError && (
                <p className="mt-3 text-sm text-red">
                  {(del.error as Error).message}
                </p>
              )}
              {retry.isError && (
                <p className="mt-3 text-sm text-red">
                  {(retry.error as Error).message}
                </p>
              )}

              {/* Documents table */}
              <div className="mt-6 overflow-x-auto rounded-xl border border-line bg-surface">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-line">
                      <th className="pl-mono-label px-4 py-3 text-left">
                        Document
                      </th>
                      <th className="pl-mono-label px-3 py-3 text-right">
                        Pages
                      </th>
                      <th className="pl-mono-label px-3 py-3 text-right">
                        Chunks
                      </th>
                      <th className="pl-mono-label px-3 py-3 text-right">
                        Figures
                      </th>
                      <th className="pl-mono-label px-4 py-3 text-left">
                        Status
                      </th>
                      <th className="pl-mono-label px-3 py-3 text-right">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={row.id}
                        className="border-b border-line-soft last:border-0"
                      >
                        <td className="px-4 py-3">
                          <span className="text-sm font-medium text-ink">
                            {row.title || row.filename}
                          </span>
                        </td>
                        <Num value={row.page_count} />
                        <Num value={row.chunk_count} />
                        <Num value={row.figure_count} />
                        <td className="px-4 py-3">
                          <StatusBadge row={row} />
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center justify-end gap-1.5">
                            {row.status === "failed" && (
                              <button
                                type="button"
                                onClick={() => retry.mutate(row.id)}
                                disabled={retry.isPending}
                                title="Retry ingestion"
                                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-accent-brand transition-colors hover:bg-accent-brand-soft disabled:opacity-50"
                              >
                                <RotateCw className="size-3.5" />
                                Retry
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => setPendingDelete(row)}
                              disabled={row.id.startsWith("optimistic-")}
                              title="Delete document"
                              aria-label={`Delete ${row.title || row.filename}`}
                              className="inline-flex items-center rounded-md p-1.5 text-ink-faint transition-colors hover:bg-red-soft hover:text-red disabled:opacity-40"
                            >
                              <Trash2 className="size-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td
                          colSpan={6}
                          className="px-4 py-10 text-center text-sm text-ink-faint"
                        >
                          {isLoading
                            ? "Loading documents…"
                            : "No documents yet — upload a PDF to get started."}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Delete confirmation. Radix Dialog controlled by `pendingDelete`. */}
      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete document?</DialogTitle>
            <DialogDescription>
              &ldquo;{pendingDelete?.title || pendingDelete?.filename}&rdquo;
              and all of its chunks, figures and vectors will be permanently
              removed. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setPendingDelete(null)}
              className="inline-flex items-center justify-center rounded-lg border border-line px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-line-soft"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirmDelete}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-red px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
            >
              <Trash2 className="size-4" />
              Delete
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
