/**
 * BFF proxy for the conversation (chat history) collection.
 *
 *   GET  /api/conversations?projectId=X → GET  {API}/v1/projects/X/conversations
 *   POST /api/conversations {project_id, title}
 *                                       → POST {API}/v1/projects/{project_id}/conversations
 *
 * List is exposed here (query-param scoped) rather than under
 * `/api/projects/[id]/conversations` so every conversation proxy route lives
 * under `/api/conversations/**`. The browser talks same-origin; the backend host
 * stays server-side (same posture as `/api/projects`).
 */

import { PAPERLENS_API_URL } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const projectId = new URL(req.url).searchParams.get("projectId");
  if (!projectId) {
    return Response.json({ detail: "projectId is required" }, { status: 400 });
  }
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/projects/${encodeURIComponent(projectId)}/conversations`,
    { headers: { accept: "application/json" }, cache: "no-store" },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(req: Request): Promise<Response> {
  const payload = (await req.json()) as {
    project_id?: string;
    title?: string | null;
  };
  const projectId = payload.project_id;
  if (!projectId) {
    return Response.json({ detail: "project_id is required" }, { status: 400 });
  }
  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/projects/${encodeURIComponent(projectId)}/conversations`,
    {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ title: payload.title ?? null }),
      cache: "no-store",
    },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
