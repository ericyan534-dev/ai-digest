# Multi-stage build for Next.js 14 App Router (no standalone output configured).
# Node version: 20-alpine, matching @types/node 20.x in package.json.
# NEXT_PUBLIC_API_BASE is baked in at build time (Next.js public env var requirement)
# and also available at runtime for any server-side usage.

# ---- Stage 1: Install dependencies ----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

# ---- Stage 2: Build ----
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* vars must be present at build time to be inlined into the bundle
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

RUN npm run build

# ---- Stage 3: Runner ----
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Runtime env — also accept NEXT_PUBLIC_API_BASE at container start
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

RUN addgroup --system app && adduser --system --ingroup app app

# Copy only what next start needs
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

USER app

EXPOSE 3000

CMD ["sh", "-c", "PORT=${PORT:-3000} npm run start -- -p ${PORT:-3000}"]
