/**
 * BFF proxy for the projects collection.
 *
 *   GET  /api/projects  → GET  {PAPERLENS_API_URL}/v1/projects
 *   POST /api/projects  → POST {PAPERLENS_API_URL}/v1/projects  {name, description}
 *
 * The browser talks same-origin; the backend host stays server-side (same
 * posture as the `/api/chat` SSE proxy).
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const upstream = await fetch(`${PAPERLENS_API_URL}/v1/projects`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(req: Request): Promise<Response> {
  const payload = await req.text();
  const upstream = await fetch(`${PAPERLENS_API_URL}/v1/projects`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: payload,
    cache: "no-store",
  });
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
