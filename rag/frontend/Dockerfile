# syntax=docker/dockerfile:1
# frontend は platform の共有パッケージを file:../../platform/packages/* で参照する。
# compose の additional_contexts（platform）を /src/platform に置き、先に共有パッケージを build する（#177）。
FROM node:22-slim AS builder
WORKDIR /src
COPY --from=platform package.json package-lock.json ./platform/
COPY --from=platform packages ./platform/packages
RUN cd platform && npm ci && npm run build
COPY package.json package-lock.json ./rag/frontend/
RUN cd rag/frontend && npm ci
COPY . ./rag/frontend/
RUN cd rag/frontend && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runner
ENV BACKEND_URL=http://backend:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /src/rag/frontend/dist /usr/share/nginx/html
EXPOSE 3000
