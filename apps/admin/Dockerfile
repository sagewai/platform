# Sagewai Admin Panel — Production Dockerfile
# Multi-stage build: install deps → build → serve with minimal runtime image
#
# Build context must include the sibling ui/ directory:
#   docker build -f admin/Dockerfile -t sagewai/admin ..
#
# Or use docker-compose.yml for a simpler build.

# ──── Stage 1: Dependencies ────
FROM node:22-alpine AS deps
WORKDIR /app

# Copy the local UI library (sibling directory)
COPY ui/ /ui/

COPY admin/package.json admin/pnpm-lock.yaml ./
RUN corepack enable pnpm && pnpm install --frozen-lockfile --prod=false

# ──── Stage 2: Build ────
FROM node:22-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY admin/ .

# Build-time env vars (override at runtime via docker run -e)
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

RUN corepack enable pnpm && pnpm build

# ──── Stage 3: Production Runtime ────
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3008

# Create non-root user
RUN addgroup --system --gid 1001 sagewai && \
    adduser --system --uid 1001 sagewai

# Copy only what's needed for production
COPY --from=builder /app/public ./public
COPY --from=builder --chown=sagewai:sagewai /app/.next/standalone ./
COPY --from=builder --chown=sagewai:sagewai /app/.next/static ./.next/static

USER sagewai

EXPOSE 3008

CMD ["node", "server.js"]
