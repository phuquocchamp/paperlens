/**
 * Backend base URL resolution for the BFF proxy routes (server-side only).
 *
 * `PAPERLENS_API_URL` points at the FastAPI `api` service. Inside docker-compose
 * that is `http://api:8000` (the compose service DNS name); for local `next dev`
 * against a locally-run backend it is `http://localhost:8000`.
 *
 * This value is read ONLY in Node route handlers (BFF proxy) — never shipped to
 * the browser, which reaches the backend exclusively through the same-origin
 * `/api/*` routes.
 */
export const PAPERLENS_API_URL =
  process.env.PAPERLENS_API_URL ?? "http://localhost:8000";

/** Same-origin BFF endpoint that `useChat` streams from. */
export const CHAT_API_ROUTE = "/api/chat";
