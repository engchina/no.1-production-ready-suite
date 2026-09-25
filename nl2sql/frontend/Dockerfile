# build context は suite（monorepo）root を想定する:
#   docker build -f nl2sql/frontend/Dockerfile .  (suite root で実行)
FROM node:22-slim AS build

ARG APP_DIR=nl2sql
ARG PLATFORM_DIR=platform

WORKDIR /app
COPY ${PLATFORM_DIR}/package.json ${PLATFORM_DIR}/package-lock.json ./${PLATFORM_DIR}/
COPY ${PLATFORM_DIR}/packages/ui/package.json ./${PLATFORM_DIR}/packages/ui/
COPY ${PLATFORM_DIR}/packages/system-settings/package.json ./${PLATFORM_DIR}/packages/system-settings/
WORKDIR /app/${PLATFORM_DIR}
RUN npm ci

COPY ${PLATFORM_DIR}/packages/ui ./packages/ui
COPY ${PLATFORM_DIR}/packages/system-settings ./packages/system-settings
# 共有 UI → 共有システム設定画面の順に build する（platform の npm run build）。
RUN npm run build

WORKDIR /app
COPY ${APP_DIR}/frontend/package.json ${APP_DIR}/frontend/package-lock.json ./${APP_DIR}/frontend/

WORKDIR /app/${APP_DIR}/frontend
RUN npm ci

COPY ${APP_DIR}/frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
ARG APP_DIR=nl2sql
COPY --from=build /app/nl2sql/frontend/dist /usr/share/nginx/html
COPY ${APP_DIR}/frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
