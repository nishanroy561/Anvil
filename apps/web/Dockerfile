FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
# `npm install`, not `ci`: no lockfile is committed for the web app.
RUN npm install


FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1

# Build-time, not runtime, and that distinction cost an afternoon: `rewrites()` in
# next.config.mjs is evaluated during `next build` and serialized into the routes
# manifest, so the /api proxy target is frozen into the image. Setting
# WEB_API_BASE_URL on the *container* has no effect — the process reads the baked
# value and, with the default, tries to reach the API at its own localhost.
#
# Compose passes `http://api:8000`. Point it elsewhere by rebuilding:
#   docker compose build --build-arg WEB_API_BASE_URL=https://anvil.internal web
ARG WEB_API_BASE_URL=http://api:8000
ENV WEB_API_BASE_URL=${WEB_API_BASE_URL}
RUN npm run build


FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `output: "standalone"` (next.config.mjs) traces the exact server dependencies
# into .next/standalone, so this image never carries node_modules or the source
# tree — only the traced server and the static assets.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

# node:alpine already ships a `node` user (uid 1000). Reusing it beats inventing
# another, and the standalone output is read-only at runtime so nothing needs to
# own it.
USER node
EXPOSE 3000

# Next answers on / once it is listening. wget is in busybox, so unlike the Python
# images this needs no interpreter trick.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
