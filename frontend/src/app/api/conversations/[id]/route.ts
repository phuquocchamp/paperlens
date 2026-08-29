/**
 * BFF proxy for a single conversation.
 *
 *   GET    /api/conversations/{id} → GET    {API}/v1/conversations/{id}
 *   PATCH  /api/conversations/{id} → PATCH  {API}/v1/conversations/{id}  {title}
 *   DELETE /api/conversations/{id} → DELETE {API}/v1/conversations/{id}
 *
 * The detail response carries `messages[].parts` verbatim so the chat view can
 * reload the full history (citations included).
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/conversations/${encodeURIComponent(id)}`,
    { headers: { accept: "application/json" }, cache: "no-store" },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const payload = await req.text();
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/conversations/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: payload,
      cache: "no-store",
    },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/conversations/${encodeURIComponent(id)}`,
    { method: "DELETE", cache: "no-store" },
  );
  // 204 has no body.
  return new Response(null, { status: upstream.status });
}
