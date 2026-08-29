# syntax=docker/dockerfile:1

# Multi-stage build for the PaperLens Next.js frontend.
# Produces a minimal standalone server (next.config `output: "standalone"`)
# listening on 3000. Agent A's compose uses `./frontend` as the build context.

# --- deps: install node_modules from lockfile ------------------------------- #
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- builder: compile the app ---------------------------------------------- #
FROM node:22-alpine AS builder
WORKDIR /app
# PAPERLENS_API_URL is baked into build-time rewrites (/static proxy); the BFF
# route handlers additionally read it at RUNTIME. Default suits docker-compose.
ARG PAPERLENS_API_URL=http://api:8000
ENV PAPERLENS_API_URL=${PAPERLENS_API_URL}
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# --- runner: standalone server --------------------------------------------- #
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Runtime default; docker-compose overrides with the `api` service URL.
ENV PAPERLENS_API_URL=http://api:8000

RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
