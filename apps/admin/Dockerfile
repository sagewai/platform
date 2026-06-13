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
FROM node:24-alpine@sha256:01743339035a5c3c11a373cd7c83aeab6ed1457b55da6a69e014a95ac4e4700b AS deps
WORKDIR /app

# CI=true tells pnpm 11 not to prompt for `node_modules` purges on TTY-less
# builds (otherwise: ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY).
ENV CI=true

# Install pnpm via corepack and fetch dependencies.
# In a monorepo the root lockfile doesn't match standalone context,
# so we install without --frozen-lockfile here. Lockfile integrity
# is validated by the CI build job (pnpm install in workspace mode).
COPY package.json ./
RUN corepack enable pnpm && pnpm install --prod=false

# ──── Stage 2: Build ───────────────────────────────────────────────────────
FROM node:24-alpine@sha256:01743339035a5c3c11a373cd7c83aeab6ed1457b55da6a69e014a95ac4e4700b AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
ENV CI=true

# Baked into the browser bundle AND the next.config CSP `connect-src` at build
# time (NEXT_PUBLIC_* and the security headers are build-time, not runtime). It
# must be reachable from the BROWSER, so for the bundled compose this is the
# published backend port — http://localhost:8000/admin — NOT the docker-internal
# `backend:8000` hostname (the browser can't resolve that, and the CSP would
# block it). Override for other origins: --build-arg NEXT_PUBLIC_ADMIN_API_URL=…
ARG NEXT_PUBLIC_ADMIN_API_URL=http://localhost:8000/admin
ENV NEXT_PUBLIC_ADMIN_API_URL=${NEXT_PUBLIC_ADMIN_API_URL}

RUN corepack enable pnpm && pnpm build

# ──── Stage 3: Production Runtime ──────────────────────────────────────────
FROM node:24-alpine@sha256:01743339035a5c3c11a373cd7c83aeab6ed1457b55da6a69e014a95ac4e4700b AS runner
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
