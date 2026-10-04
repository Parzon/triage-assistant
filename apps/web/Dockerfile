# Base images are pinned by digest as well as tag: a tag can be moved to
# other content, a digest cannot. Dependabot moves both, in one PR. Node's
# major follows .nvmrc.

# ---- deps: node_modules from the lockfile, shared by every later stage ----
FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# ---- dev: Vite dev server; compose bind-mounts the source over /app -------
FROM deps AS dev
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
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
# container itself never needs root. The -slim variant: the same nginx
# without the optional modules (njs, xslt, image filter, geoip) and curl,
# which this config never loads - and which brought every known
# vulnerability the full variant had (make scan).
FROM nginxinc/nginx-unprivileged:1.31-alpine-slim@sha256:c81a27f28bc2d9c2da8998444e653c7b85b9bbbaa92e44ef18d8920784e06507 AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/snippets/ /etc/nginx/snippets/
COPY --chmod=755 nginx/entrypoint/25-runtime-conf.sh /docker-entrypoint.d/
EXPOSE 8080
# 127.0.0.1, not localhost: busybox wget may try ::1 first, and nginx
# listens on IPv4 only, which would mark a healthy container unhealthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
