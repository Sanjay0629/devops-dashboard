# syntax=docker/dockerfile:1
# See backend/Dockerfile: exact tags, digests still to be pinned (AD-16).
FROM node:22.12.0-alpine AS base
ENV CI=true
WORKDIR /app
RUN apk add --no-cache curl

# ---------------------------------------------------------------- development
FROM base AS development
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN addgroup -g 10001 dcp && adduser -D -u 10001 -G dcp dcp && chown -R dcp:dcp /app
USER dcp
EXPOSE 5173
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:5173/ || exit 1
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------------------------------------------------------------- build
FROM base AS build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---------------------------------------------------------------- production
# Static assets only. In production the reverse proxy serves these and routes
# /api to the backend, so the SPA and API share one origin
# (docs/ARCHITECTURE.md §2.1).
FROM nginx:1.27.3-alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1
