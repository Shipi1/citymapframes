# syntax=docker/dockerfile:1
# ── CityMapFrames web (Svelte SPA) ──────────────────────────────────────────
# Two-stage build: Node compiles the Vite bundle, nginx serves the static
# output. The runtime image is ~30 MB (alpine nginx + ~1 MB of JS/CSS).
#
# Build:   docker build -t citymapframes-web -f web/Dockerfile web
# (handled by docker-compose.yml — context is web/, not the project root)
# ────────────────────────────────────────────────────────────────────────────

# --- build stage ------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Install deps first (layer-cache friendly).
COPY package.json package-lock.json ./
RUN npm ci

# Build-time arguments — defaults assume deployment at a dedicated
# (sub)domain like map.shipisnature.com, where the SPA owns the URL root
# and the API hangs off /api/. Override via docker-compose `build.args`
# if you ever want a path-prefix layout instead (e.g. /map/ + /api/map/).
ARG VITE_BASE_PATH=/
ARG VITE_API_BASE=/api
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
ENV VITE_API_BASE=${VITE_API_BASE}

# Copy the rest and build. vite reads VITE_* env vars at build time and
# inlines them into the bundle — they are NOT runtime-configurable.
COPY . ./
RUN npm run build

# --- runtime stage ----------------------------------------------------------
FROM nginx:1.27-alpine

# Replace the default conf with our SPA-aware one (handles client-side
# routing fallback to index.html).
COPY nginx-container.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine already declares its own CMD; no need to repeat.
