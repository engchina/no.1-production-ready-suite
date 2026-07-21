# build context は workspace root を想定する:
#   docker build -f no.1-production-ready-nl2sql/frontend/Dockerfile .
FROM node:22-slim AS build

ARG APP_DIR=no.1-production-ready-nl2sql
ARG PLATFORM_DIR=no.1-production-ready-platform

WORKDIR /app
COPY ${PLATFORM_DIR}/packages/ui ./${PLATFORM_DIR}/packages/ui
COPY ${APP_DIR}/frontend/package.json ${APP_DIR}/frontend/package-lock.json ./${APP_DIR}/frontend/

WORKDIR /app/${APP_DIR}/frontend
RUN npm ci

COPY ${APP_DIR}/frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
ARG APP_DIR=no.1-production-ready-nl2sql
COPY --from=build /app/no.1-production-ready-nl2sql/frontend/dist /usr/share/nginx/html
COPY ${APP_DIR}/frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
