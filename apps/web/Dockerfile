ARG NODE_VERSION=24

# ---- deps: node_modules from the lockfile, shared by every later stage ----
FROM node:${NODE_VERSION}-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# ---- dev: Vite dev server; compose bind-mounts the source over /app -------
FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---- build: type-check + production bundle into /app/dist -----------------
FROM deps AS build
COPY . .
RUN npm run build

# ---- production: static files + reverse proxy, non-root on 8080 -----------
# nginx-unprivileged instead of nginx: the official image starts as root to
# bind port 80. Mapping host port 80 -> 8080 is done by Docker, so the
# container itself never needs root.
FROM nginxinc/nginx-unprivileged:1.30-alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/snippets/ /etc/nginx/snippets/
EXPOSE 8080
# 127.0.0.1, not localhost: busybox wget may try ::1 first, and nginx
# listens on IPv4 only, which would mark a healthy container unhealthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
