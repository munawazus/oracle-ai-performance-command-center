# Build context is the repository root (see docker-compose.yml) so the prod stage can also
# reach deployment/nginx.conf.

# --- dev: what docker-compose.yml runs by default (vite dev server, source bind-mounted) ---
FROM node:22-alpine AS dev
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# --- build: production static assets ---
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend .
RUN npm run build

# --- prod: nginx serving the built assets (Phase 16 / deployment hardening target) ---
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY deployment/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
