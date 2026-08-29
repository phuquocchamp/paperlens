/**
 * BFF proxy for the Stop button.
 *
 * `useChat().stop()` only aborts the browser fetch — CONTRACT §2 is explicit
 * that "disconnect ≠ cancel": backend generation runs detached and is only
 * actually stopped by POST /v1/chat/streams/{id}/stop. The browser cannot reach
 * `http://api:8000`, so this same-origin route proxies that idempotent call.
 *
 * Always resolves 200-ish: the Stop button must never surface an error.
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params; // Next.js 15: params is async

  try {
    const upstream = await fetch(
      `${PAPERLENS_API_URL}/v1/chat/streams/${encodeURIComponent(id)}/stop`,
      { method: "POST", cache: "no-store" },
    );
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    // Soft-fail: never let the Stop control error out.
    return Response.json({ stream_id: id, stopped: false }, { status: 200 });
  }
}
