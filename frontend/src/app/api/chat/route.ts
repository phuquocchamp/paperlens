/**
 * BFF proxy for the chat answer stream.
 *
 * `useChat` (browser) POSTs here; this route streams the request body straight
 * through to the FastAPI `api` service and returns the upstream SSE body
 * verbatim. The browser never talks to the backend directly (localhost-only
 * security posture, README) — everything crosses this same-origin boundary.
 *
 * Required flags (CONTRACT §6):
 *   - runtime 'nodejs'        (streaming needs the Node runtime, not Edge here)
 *   - dynamic 'force-dynamic' (never cache/prerender a stream)
 *   - maxDuration 300         (long-lived SSE)
 *   - duplex 'half' + req.body forwarded WITHOUT buffering (don't re-JSON)
 *   - signal: req.signal      (client disconnect aborts the upstream fetch)
 *   - x-accel-buffering: no   (disable nginx proxy buffering of the SSE)
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** Response header the backend uses to surface the stream id (chat.py). */
const STREAM_ID_HEADER = "x-paperlens-stream-id";

export async function POST(req: Request): Promise<Response> {
  const upstreamUrl = `${PAPERLENS_API_URL}/v1/chat/streams`;

  const upstream = await fetch(upstreamUrl, {
    method: "POST",
    headers: {
      "content-type": req.headers.get("content-type") ?? "application/json",
      accept: "text/event-stream",
    },
    body: req.body,
    signal: req.signal,
    cache: "no-store",
    // duplex is required by the fetch spec when sending a stream body; the type
    // is not yet in lib.dom, hence the cast.
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  const headers = new Headers();
  headers.set(
    "content-type",
    upstream.headers.get("content-type") ?? "text/event-stream",
  );
  headers.set("cache-control", "no-cache, no-transform");
  headers.set("connection", "keep-alive");
  headers.set("x-accel-buffering", "no");

  // Forward the stream id so the client's custom fetch can capture it and later
  // address POST /api/chat/streams/{id}/stop (same-origin header is readable).
  const streamId = upstream.headers.get(STREAM_ID_HEADER);
  if (streamId) headers.set(STREAM_ID_HEADER, streamId);

  return new Response(upstream.body, { status: upstream.status, headers });
}
