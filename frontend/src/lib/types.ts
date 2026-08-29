/**
 * TypeScript mirror of the backend `GenEvent` domain types
 * (`backend/app/domain/events.py`) as they arrive over the AI SDK UI Message
 * Stream (CONTRACT.md §2 / §4).
 *
 * NAMING: the backend serializes Pydantic models as snake_case
 * (`verify_status`, `document_id`, `image_url`, `structure_kind`,
 * `content_hash`). The AI SDK does not touch the inner `data` object, so these
 * names reach the client verbatim — mirror them EXACTLY, do NOT camelCase.
 *
 * data-status is the one field where CONTRACT §2 (`detail`) and the AI SDK
 * example (`message`) disagree; the contract wins — this uses `detail`.
 */

import type { UIMessage } from "ai";

// --------------------------------------------------------------------------- //
// Enums (string unions mirroring the Python Enums)
// --------------------------------------------------------------------------- //
export type Stage =
  | "moderating"
  | "retrieving"
  | "reranking"
  | "reading_figures"
  | "generating";

export type VerifyStatus =
  | "pending"
  | "grounded"
  | "weak"
  | "unverifiable"
  | "source_changed";

export type FigureDisplay = "candidate" | "cited";

export type TableStructure = "flat" | "spanned";

// --------------------------------------------------------------------------- //
// Data-part payloads (the `data` field of each AI SDK data part)
// --------------------------------------------------------------------------- //

/** Transient — delivered ONLY through `onData` (never lands in message.parts). */
export interface StatusData {
  stage: Stage;
  detail?: string | null; // e.g. "Searching 4 documents…" (CONTRACT §2 uses `detail`)
}

/** The single source of truth for a citation (CitationEvent). */
export interface CitationData {
  id: string; // e.g. "cit-C3"
  marker: string; // e.g. "C3" — matches MARKER_RE capture group
  quote: string; // verbatim, server-dereferenced from chunks.content
  document_id: string;
  document_title: string;
  filename: string;
  page: number;
  section_path?: string | null;
  content_hash: string; // drift ⇒ verify_status becomes "source_changed"
  verify_status: VerifyStatus;
}

export interface FigureData {
  id: string; // e.g. "fig-{uuid}"
  marker?: string | null; // e.g. "F2" once cited
  label: string; // e.g. "Figure 2"
  image_url: string;
  page: number;
  caption?: string | null;
  width?: number | null; // set ahead of load to avoid layout shift
  height?: number | null;
  display: FigureDisplay;
}

export interface TableData {
  id: string; // e.g. "tbl-{uuid}"
  marker?: string | null; // e.g. "T1"
  label: string;
  page: number;
  caption?: string | null;
  structure_kind: TableStructure;
  html?: string | null; // populated when structure_kind === "spanned"
  markdown?: string | null; // populated when structure_kind === "flat"
}

export interface NoticeData {
  text: string;
}

/** Typed error. `message` MUST NOT leak provider/stack details (server-enforced). */
export interface ErrorData {
  error_type: string; // e.g. "rate_limited", "upstream_unavailable"
  message: string;
}

/** messageMetadata carried on the `start` frame. */
export interface PaperLensMetadata {
  request_id: string;
  model: string;
}

/**
 * The DATA_PARTS map: key `foo` becomes part type `data-foo` with `.data` typed
 * as the mapped payload. Passed as the second generic to `UIMessage`.
 *
 * MUST be a `type` alias, not an `interface`: only type-aliased object literals
 * get the implicit index signature that satisfies the SDK's
 * `UIDataTypes = Record<string, unknown>` constraint (and lets
 * `InferUIMessageData` recover these payloads instead of widening to unknown).
 */
export type PaperLensDataParts = {
  status: StatusData;
  citation: CitationData;
  figure: FigureData;
  table: TableData;
  notice: NoticeData;
  error: ErrorData;
};

/** The fully-typed message used everywhere `useChat` is consumed. */
export type PaperLensUIMessage = UIMessage<PaperLensMetadata, PaperLensDataParts>;

export type PaperLensUIPart = PaperLensUIMessage["parts"][number];
