# Sagewai Admin Panel — Production Dockerfile
#
# Multi-stage build: install deps → build → serve with a minimal runtime image.
#
# Build from the `admin/` directory as its own context:
#   docker build -t ghcr.io/sagewai/admin:latest .
#
# Runtime configuration:
#   NEXT_PUBLIC_ADMIN_API_URL   URL of the sagewai backend (e.g. http://host.docker.internal:8000/admin)
#   PORT                         HTTP port to listen on (default 3008)
#
# Run:
#   docker run --rm -p 3008:3008 \
#     -e NEXT_PUBLIC_ADMIN_API_URL=http://host.docker.internal:8000/admin \
#     ghcr.io/sagewai/admin:latest

# ──── Stage 1: Dependencies ────────────────────────────────────────────────
FROM node:22-alpine@sha256:4d64b49e6c891c8fc821007cb1cdc6c0db7773110ac2c34bf2e6960adef62ed3 AS deps
WORKDIR /app

# Install pnpm via corepack and fetch dependencies using the frozen lockfile.
# Copy only the manifest + lockfile so this layer is cached until deps change.
COPY package.json pnpm-lock.yaml ./
RUN corepack enable pnpm && pnpm install --frozen-lockfile --prod=false

# ──── Stage 2: Build ───────────────────────────────────────────────────────
FROM node:22-alpine@sha256:4d64b49e6c891c8fc821007cb1cdc6c0db7773110ac2c34bf2e6960adef62ed3 AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

RUN corepack enable pnpm && pnpm build

# ──── Stage 3: Production Runtime ──────────────────────────────────────────
FROM node:22-alpine@sha256:4d64b49e6c891c8fc821007cb1cdc6c0db7773110ac2c34bf2e6960adef62ed3 AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3008

# Non-root user
RUN addgroup --system --gid 1001 sagewai && \
    adduser  --system --uid 1001 sagewai

# Copy only what's needed for production (Next standalone output)
COPY --from=builder /app/public ./public
COPY --from=builder --chown=sagewai:sagewai /app/.next/standalone ./
COPY --from=builder --chown=sagewai:sagewai /app/.next/static ./.next/static

USER sagewai

EXPOSE 3008

CMD ["node", "server.js"]
