/**
 * BFF proxy for a project's documents.
 *
 *   GET  /api/projects/{id}/documents → GET  {API}/v1/projects/{id}/documents
 *   POST /api/projects/{id}/documents → POST {API}/v1/projects/{id}/documents
 *                                       (multipart file upload)
 *
 * IMPORTANT (multipart): re-post the parsed `FormData` and let `fetch`
 * regenerate the `multipart/form-data` boundary. Forwarding a hand-set
 * `content-type` here (with the client's old boundary) makes the backend 422.
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
    `${PAPERLENS_API_URL}/v1/projects/${id}/documents`,
    { headers: { accept: "application/json" }, cache: "no-store" },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;

  // Parse the incoming multipart body, then re-post the FormData so fetch sets
  // a fresh boundary. Do NOT copy the client's content-type header.
  const form = await req.formData();

  const upstream = await fetch(
    `${PAPERLENS_API_URL}/v1/projects/${id}/documents`,
    {
      method: "POST",
      headers: { accept: "application/json" },
      body: form,
      cache: "no-store",
    },
  );
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
