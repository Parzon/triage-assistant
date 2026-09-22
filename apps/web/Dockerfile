# Shared base: install deps once, reused by both the dev and production
# paths below so neither has to repeat the npm ci step.
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- dev: what docker-compose uses locally (source bind-mounted over this) ----
FROM node:20-slim AS dev
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---- build: compiles the production static bundle ----
FROM deps AS build
COPY . .
RUN npm run build

# ---- production: nginx serving the static bundle + reverse-proxying /api ----
# nginxinc/nginx-unprivileged, not plain nginx: the official image runs
# as root to bind port 80 (<1024 needs a privileged port bind); the
# unprivileged variant runs as a real non-root user on 8080 instead -
# same non-root hardening as the api image, applied consistently here too.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost:8080/ >/dev/null 2>&1 || exit 1
