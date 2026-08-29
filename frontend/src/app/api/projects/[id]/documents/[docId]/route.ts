/**
 * BFF proxy for a single document.
 *
 *   DELETE /api/projects/{id}/documents/{docId}
 *        → DELETE {API}/v1/projects/{id}/documents/{docId}
 *
 * The browser talks same-origin; the backend host stays server-side (same
 * posture as the sibling collection proxy and the `/api/chat` SSE proxy). The
 * backend returns 204 with no body on success — forward the status verbatim.
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string; docId: string }> },
): Promise<Response> {
  const { id, docId } = await params;
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/projects/${id}/documents/${docId}`,
    { method: "DELETE", headers: { accept: "application/json" }, cache: "no-store" },
  );
  const body = await upstream.text();
  return new Response(body || null, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
