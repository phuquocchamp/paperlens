/**
 * BFF proxy for retrying a FAILED document.
 *
 *   POST /api/projects/{id}/documents/{docId}/retry
 *        → POST {API}/v1/projects/{id}/documents/{docId}/retry
 *
 * The backend re-queues a FAILED document (200 + updated DocumentOut); REJECTED
 * / in-flight / ready documents return 409. Forward the status verbatim so the
 * client surfaces the reason.
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string; docId: string }> },
): Promise<Response> {
  const { id, docId } = await params;
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/projects/${id}/documents/${docId}/retry`,
    { method: "POST", headers: { accept: "application/json" }, cache: "no-store" },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
