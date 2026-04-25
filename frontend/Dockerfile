# syntax=docker/dockerfile:1
# Build: docker build --build-arg NEXT_PUBLIC_API_URL=http://backend:8000 .
# `NEXT_PUBLIC_API_URL` must be the **container-to-container** base URL
# the Next.js route handlers use to reach the API in Docker Compose
# (service name `backend`, port 8000).

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=http://backend:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Invoke `next` directly so the image does not use `next build --turbopack`
# from package.json (webpack build is a better default in CI/Docker).
ENV NEXT_TELEMETRY_DISABLED=1
RUN npx next build

FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
  && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
