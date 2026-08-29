import type { NextConfig } from "next";

/** Backend base URL for server-side rewrites (see src/lib/api.ts). */
const PAPERLENS_API_URL =
  process.env.PAPERLENS_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Standalone output → a minimal self-contained server for the Docker image.
  output: "standalone",

  async rewrites() {
    // Proxy the backend's static assets (figure crops, etc.) through the same
    // origin so <img src="/static/..."> loads without exposing the api service.
    return [
      {
        source: "/static/:path*",
        destination: `${PAPERLENS_API_URL}/static/:path*`,
      },
    ];
  },
};

export default nextConfig;
