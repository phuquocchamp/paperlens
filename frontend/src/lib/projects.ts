/**
 * Client-side types and same-origin fetchers for the projects + documents REST
 * surface. The browser NEVER talks to the FastAPI backend directly — it goes
 * through the same-origin BFF proxy routes under `/api/*` (see
 * `app/api/projects/**`), which forward to `PAPERLENS_API_URL` server-side. This
 * keeps the backend host/secret off the client, consistent with the existing
 * `/api/chat` SSE proxy.
 *
 * Field names mirror the FastAPI `ProjectOut` / `DocumentOut` schemas verbatim
 * (snake_case — do NOT camelCase).
 */

// --------------------------------------------------------------------------- //
// Types (mirror backend ProjectOut / DocumentOut)
// --------------------------------------------------------------------------- //
export interface Project {
  id: string;
  name: string;
  description?: string | null;
  created_at?: string | null;
}

/** Top-level ingestion status — mirrors backend `DocumentStatus` values. */
export type DocumentStatus =
  | "uploaded"
  | "queued"
  | "processing"
  | "text_ready"
  | "full_ready"
  | "rejected"
  | "failed"
  | "deleted";

/** PROCESSING sub-stage — mirrors backend `IngestStage` values (null otherwise). */
export type IngestStage =
  | "parsing"
  | "cropping"
  | "captioning"
  | "chunking"
  | "embedding"
  | "indexing";

export interface DocumentRow {
  id: string;
  project_id: string;
  title?: string | null;
  filename: string;
  file_sha256?: string | null;
  page_count?: number | null;
  status: DocumentStatus | string;
  stage?: IngestStage | string | null;
  progress?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  chunk_count: number;
  figure_count: number;
  created_at?: string | null;
}

// --------------------------------------------------------------------------- //
// Terminal-status helpers (drive polling + composer gating)
// --------------------------------------------------------------------------- //

/**
 * A document that no longer changes on its own — polling can stop once every
 * document is terminal. `text_ready` is intentionally NOT terminal here: the
 * worker continues captioning toward `full_ready`, so `figure_count` keeps
 * moving. It is instead placed on the slowest poll tier by `pollIntervalFor`.
 */
export function isTerminal(status: string): boolean {
  return (
    status === "full_ready" ||
    status === "rejected" ||
    status === "failed" ||
    status === "deleted"
  );
}

/** A document that can answer questions (chat opens at `text_ready`). */
export function isQueryable(status: string): boolean {
  return status === "text_ready" || status === "full_ready";
}

// --------------------------------------------------------------------------- //
// Same-origin fetchers (called from React Query hooks in the browser)
// --------------------------------------------------------------------------- //

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : JSON.stringify(body?.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(
      `Request failed (${res.status})${detail ? `: ${detail}` : ""}`,
    );
  }
  return res.json() as Promise<T>;
}

export async function fetchProjects(signal?: AbortSignal): Promise<Project[]> {
  return jsonOrThrow<Project[]>(
    await fetch("/api/projects", { signal, cache: "no-store" }),
  );
}

export async function createProject(input: {
  name: string;
  description?: string;
}): Promise<Project> {
  return jsonOrThrow<Project>(
    await fetch("/api/projects", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function fetchDocuments(
  projectId: string,
  signal?: AbortSignal,
): Promise<DocumentRow[]> {
  return jsonOrThrow<DocumentRow[]>(
    await fetch(`/api/projects/${projectId}/documents`, {
      signal,
      cache: "no-store",
    }),
  );
}

export async function uploadDocument(
  projectId: string,
  file: File,
): Promise<DocumentRow> {
  const fd = new FormData();
  fd.append("file", file);
  return jsonOrThrow<DocumentRow>(
    await fetch(`/api/projects/${projectId}/documents`, {
      method: "POST",
      body: fd,
    }),
  );
}

// --------------------------------------------------------------------------- //
// React Query keys
// --------------------------------------------------------------------------- //
export const queryKeys = {
  projects: ["projects"] as const,
  documents: (projectId: string) => ["documents", projectId] as const,
};
