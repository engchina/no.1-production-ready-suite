#!/usr/bin/env bash
# OCI Resource Manager の統合 stack（terraform/stack、#217）の cloud-init から呼ばれる rag の Compute 初期化スクリプト。
# RAG は rag/docker-compose.yml で動かす（parser / 前処理は独立したマイクロサービスのため）。
#   - backend / ingestion-worker / 前処理 / CPU parser: Docker Compose（backend は 127.0.0.1:8000 だけに公開）
#   - frontend: node の container で静的 build し、host の Nginx が配信して /api/ を backend へ proxy する
# ADB の DDL は持たない。RAG の system schema はアプリの CLI（app.rag.system_schema_cli）で適用する。
# GPU の service（compose の gpu profile）は起動しない。
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [ "${RAG_INIT_TEST_MODE:-false}" != "true" ]; then
  exec > >(tee -a /var/log/rag-init.log) 2>&1
fi

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
# RAG と共有 platform は monorepo（no.1-production-ready-suite）の rag/ と platform/ にある。
SUITE_REPO_DIR="${APP_ROOT}/no.1-production-ready-suite"
APP_REPO_DIR="${SUITE_REPO_DIR}/rag"
BACKEND_DIR="${APP_REPO_DIR}/backend"
FRONTEND_DIR="${APP_REPO_DIR}/frontend"
WALLET_DIR="${APP_ROOT}/wallet"
PROPS_DIR="${APP_ROOT}/props"
DEPLOY_DIR="${DEPLOY_DIR:-${APP_ROOT}/rag-deploy}"
COMPOSE_OVERRIDE_FILE="${DEPLOY_DIR}/docker-compose.oci.yml"
COMPOSE_PROJECT_NAME="production-ready-rag"
COMPOSE_WRAPPER="${COMPOSE_WRAPPER:-/usr/local/bin/rag-compose}"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
APPLICATION_PORT="${APPLICATION_PORT:-$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/application_port.txt" || printf '80')}"
NODE_IMAGE="${NODE_IMAGE:-node:22-slim}"
DOCKER_APT_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"
DOCKER_APT_REPO_URL="https://download.docker.com/linux/ubuntu"
DOCKER_KEYRING_PATH="${DOCKER_KEYRING_PATH:-/etc/apt/keyrings/docker.asc}"
DOCKER_SOURCE_PATH="${DOCKER_SOURCE_PATH:-/etc/apt/sources.list.d/docker.list}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
NGINX_SITES_AVAILABLE_DIR="${NGINX_SITES_AVAILABLE_DIR:-/etc/nginx/sites-available}"
NGINX_SITES_ENABLED_DIR="${NGINX_SITES_ENABLED_DIR:-/etc/nginx/sites-enabled}"
COMPOSE_SERVICE="production-ready-rag.service"
# backend の MAX_UPLOAD_BYTES（既定 200 MiB）より少し大きくする。
NGINX_CLIENT_MAX_BODY_SIZE="210M"
DATABASE_INITIALIZATION_READY=false

# この stack が起動してよい service（rag/docker-compose.yml の CPU / OCI の service だけ）。
# GPU の service はここに含めない。compose_services.txt にこれ以外の名前があれば失敗させる。
ALLOWED_COMPOSE_SERVICES=(
  backend
  ingestion-worker
  preprocess-office-to-pdf
  preprocess-pdf-to-page-images
  preprocess-csv-to-json
  preprocess-excel-to-json
  preprocess-url-to-markdown
  preprocess-image-enhance
  preprocess-pii-redact
  parser-unstructured
  parser-docling
  parser-marker
  parser-oci-genai-vision
  parser-oci-document-understanding
  pipeline-generation
  pipeline-retrieval
)
REQUIRED_COMPOSE_SERVICES=(backend ingestion-worker parser-unstructured)
COMPOSE_SERVICES=()

PATH="/usr/local/bin:/usr/bin:/bin:${PATH}"

log() {
  printf '[rag-init] %s\n' "$*"
}

trap 'status=$?; log "Initialization failed at line ${LINENO} with status ${status}: ${BASH_COMMAND}"; exit "${status}"' ERR

wait_for_apt_availability() {
  local locks=(
    /var/lib/dpkg/lock
    /var/lib/dpkg/lock-frontend
    /var/lib/apt/lists/lock
    /var/cache/apt/archives/lock
  )
  local elapsed=0
  local timeout=600

  while fuser "${locks[@]}" >/dev/null 2>&1; do
    if [ "${elapsed}" -ge "${timeout}" ]; then
      log "Timed out waiting for apt/dpkg locks."
      return 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
}

retry_command() {
  local attempts="$1"
  shift
  local attempt status
  status=0
  for attempt in $(seq 1 "${attempts}"); do
    set +e
    "$@"
    status="$?"
    set -e
    if [ "${status}" -eq 0 ]; then
      return 0
    fi
    log "Command failed with status ${status} on attempt ${attempt}/${attempts}: $*"
    sleep $((attempt * 5))
  done
  return "${status}"
}

apt_get() {
  wait_for_apt_availability
  retry_command 5 apt-get "$@"
}

install_system_packages() {
  log "Installing system packages."
  apt_get update
  apt_get install -y \
    ca-certificates \
    curl \
    git \
    gnupg \
    nginx \
    netfilter-persistent \
    openssl \
    unzip
}

# Docker Engine と compose / buildx plugin は Docker 公式の apt repository から入れる
# （docker-compose.yml は build の additional_contexts と `!override` を使うため、新しい compose が必要）。
install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker $(docker --version) with $(docker compose version) is already installed."
  else
    local codename
    log "Installing Docker Engine from the official apt repository."
    install -d -m 0755 "$(dirname "${DOCKER_KEYRING_PATH}")" "$(dirname "${DOCKER_SOURCE_PATH}")"
    retry_command 5 curl -fsSL "${DOCKER_APT_KEY_URL}" -o "${DOCKER_KEYRING_PATH}"
    chmod 0644 "${DOCKER_KEYRING_PATH}"
    # shellcheck source=/dev/null
    codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME}}")"
    printf 'deb [arch=%s signed-by=%s] %s %s stable\n' \
      "$(dpkg --print-architecture)" "${DOCKER_KEYRING_PATH}" "${DOCKER_APT_REPO_URL}" "${codename}" \
      > "${DOCKER_SOURCE_PATH}"
    apt_get update
    apt_get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  systemctl enable --now docker
  docker compose version
}

load_compose_services() {
  local names=()
  local name
  local allowed
  local found

  read -r -a names <<< "$(tr '\n' ' ' < "${PROPS_DIR}/compose_services.txt" 2>/dev/null || true)"
  COMPOSE_SERVICES=()
  for name in "${names[@]}"; do
    found=false
    for allowed in "${ALLOWED_COMPOSE_SERVICES[@]}"; do
      if [ "${name}" = "${allowed}" ]; then
        found=true
        break
      fi
    done
    if [ "${found}" != "true" ]; then
      log "compose service is not allowed by this stack: ${name}"
      return 1
    fi
    COMPOSE_SERVICES+=("${name}")
  done
  for name in "${REQUIRED_COMPOSE_SERVICES[@]}"; do
    if ! printf '%s\n' "${COMPOSE_SERVICES[@]}" | grep -qx "${name}"; then
      log "Required compose service is missing from ${PROPS_DIR}/compose_services.txt: ${name}"
      return 1
    fi
  done
  log "Compose services: ${COMPOSE_SERVICES[*]}"
}

prepare_filesystem() {
  log "Preparing application directories."
  if ! id "${APP_USER}" >/dev/null 2>&1; then
    log "Application user ${APP_USER} does not exist."
    return 1
  fi

  chown "root:${APP_GROUP}" "${APP_ROOT}"
  chmod 0775 "${APP_ROOT}"
  install -d -m 0755 -o root -g root "${DEPLOY_DIR}"
  install -d -m 0700 "${WALLET_DIR}"
  chown -R "${APP_USER}:${APP_GROUP}" "${SUITE_REPO_DIR}"
}

# instance 上で1回だけ生成し、再実行でも同じ値を使う（ログイン中の session を保つため）。
ensure_generated_secret() {
  local path="$1"
  if [ ! -s "${path}" ]; then
    (umask 077 && openssl rand -hex 32 > "${path}")
  fi
  chmod 0600 "${path}"
  tr -d '[:space:]' < "${path}"
}

set_env_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "${env_file}"; then
    sed -i "s|^${key}=.*$|${key}=${value}|" "${env_file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${env_file}"
  fi
}

# rag/backend/.env は docker compose の env_file。値は Resource Manager の入力から作り、
# 空の AUTH_SESSION_SECRET / AUDIT_CONTEXT_HASH_SALT だけを instance 上で生成した値で補う。
install_runtime_env() {
  local env_file="${BACKEND_DIR}/.env"
  local current

  log "Installing backend environment."
  install -m 0600 -o "${APP_USER}" -g "${APP_GROUP}" "${PROPS_DIR}/backend.env" "${env_file}"

  current="$(sed -n 's/^AUTH_SESSION_SECRET=//p' "${env_file}" | tail -n 1)"
  if [ -z "${current}" ]; then
    set_env_value "${env_file}" AUTH_SESSION_SECRET "$(ensure_generated_secret "${PROPS_DIR}/auth_session_secret")"
  fi
  current="$(sed -n 's/^AUDIT_CONTEXT_HASH_SALT=//p' "${env_file}" | tail -n 1)"
  if [ -z "${current}" ]; then
    set_env_value "${env_file}" AUDIT_CONTEXT_HASH_SALT "$(ensure_generated_secret "${PROPS_DIR}/audit_context_hash_salt")"
  fi
}

# rag/docker-compose.yml は変更せず、OCI の Compute で必要な差分だけを override で重ねる。
#   - backend は host の Nginx からだけ使うため 127.0.0.1 に bind する（既定の 0.0.0.0:8000 を置き換える）
#   - Wallet（mTLS）を backend と ingestion-worker に mount する（ORACLE_WALLET_DIR と同じ path）
#   - reboot 後も戻るよう restart policy を付ける
write_compose_override() {
  log "Writing ${COMPOSE_OVERRIDE_FILE}."
  install -d -m 0755 "${DEPLOY_DIR}"
  cat > "${COMPOSE_OVERRIDE_FILE}" <<EOF
# init_script.sh が生成する（手で編集しない）。rag/docker-compose.yml に重ねる OCI Compute 用の差分。
services:
  backend:
    ports: !override
      - "${BACKEND_HOST}:${BACKEND_PORT}:8000"
    restart: unless-stopped
    volumes:
      - ${WALLET_DIR}:${WALLET_DIR}
  ingestion-worker:
    restart: unless-stopped
    volumes:
      - ${WALLET_DIR}:${WALLET_DIR}
EOF
  chmod 0644 "${COMPOSE_OVERRIDE_FILE}"

  cat > "${COMPOSE_WRAPPER}" <<EOF
#!/usr/bin/env bash
# Production Ready RAG の docker compose（init_script.sh が生成する）。
set -euo pipefail
exec docker compose \\
  --project-name ${COMPOSE_PROJECT_NAME} \\
  --project-directory ${APP_REPO_DIR} \\
  -f ${APP_REPO_DIR}/docker-compose.yml \\
  -f ${COMPOSE_OVERRIDE_FILE} \\
  "\$@"
EOF
  chmod 0755 "${COMPOSE_WRAPPER}"
}

build_images() {
  log "Building Docker images: ${COMPOSE_SERVICES[*]}"
  retry_command 3 "${COMPOSE_WRAPPER}" build "${COMPOSE_SERVICES[@]}"
}

# container は image の appuser で動く。Wallet と OCI 設定の volume を appuser が読めるようにする。
prepare_container_permissions() {
  local container_owner

  container_owner="$("${COMPOSE_WRAPPER}" run --rm --no-deps -T --entrypoint sh backend -c 'printf "%s:%s" "$(id -u)" "$(id -g)"')"
  if ! printf '%s\n' "${container_owner}" | grep -Eq '^[0-9]+:[0-9]+$'; then
    log "Unable to resolve the backend container user: ${container_owner}"
    return 1
  fi
  log "Installing wallet for container user ${container_owner}."

  rm -rf "${WALLET_DIR}"
  install -d -m 0700 "${WALLET_DIR}"
  unzip -oq "${PROPS_DIR}/wallet.zip" -d "${WALLET_DIR}"
  chown -R "${container_owner}" "${WALLET_DIR}"
  find "${WALLET_DIR}" -type d -exec chmod 0700 {} \;
  find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;

  # OCI 設定の named volume は空で作られると root 所有になるため、appuser に渡す。
  "${COMPOSE_WRAPPER}" run --rm --no-deps -T --user root --entrypoint sh backend \
    -c 'chown appuser:appuser /home/appuser/.oci && chmod 0700 /home/appuser/.oci'
}

# RAG の system schema（table / vector index / Oracle Text）はアプリの CLI が冪等に作成・更新する。
# 失敗しても backend は起動でき、画面（システム設定 > データベース > RAG システムテーブル）からも初期化できる。
initialize_database_schema() {
  log "Initializing RAG system schema (idempotent)."
  if retry_command 5 "${COMPOSE_WRAPPER}" run --rm --no-deps -T backend \
    uv run --no-sync python -m app.rag.system_schema_cli initialize; then
    DATABASE_INITIALIZATION_READY=true
    log "RAG system schema is ready."
    return 0
  fi

  DATABASE_INITIALIZATION_READY=false
  log "WARNING: RAG system schema initialization failed. Check ADB reachability and ${BACKEND_DIR}/.env."
  log "Recovery: sudo ${COMPOSE_WRAPPER} run --rm --no-deps -T backend uv run --no-sync python -m app.rag.system_schema_cli initialize"
  log "Recovery: or open System settings > Database > RAG system tables in the application."
  return 0
}

# frontend は file:../../platform/packages/ui に依存するため、monorepo 全体を mount した node の
# container で platform と frontend を build する。host に Node.js は入れない。
build_frontend() {
  local owner
  owner="$(id -u "${APP_USER}"):$(id -g "${APP_USER}")"

  log "Building shared UI package and RAG frontend in ${NODE_IMAGE}."
  retry_command 3 docker run --rm \
    --user "${owner}" \
    -e HOME=/tmp \
    -e npm_config_cache=/tmp/.npm \
    -v "${SUITE_REPO_DIR}:/workspace" \
    -w /workspace \
    "${NODE_IMAGE}" \
    sh -c 'cd platform && npm ci && npm run build && cd ../rag/frontend && npm ci && npm run build'
  test -f "${FRONTEND_DIR}/dist/index.html"
}

configure_systemd() {
  log "Writing systemd unit for docker compose."
  cat > "${SYSTEMD_UNIT_DIR}/${COMPOSE_SERVICE}" <<EOF
[Unit]
Description=Production Ready RAG (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_REPO_DIR}
ExecStart=${COMPOSE_WRAPPER} up -d --no-build ${COMPOSE_SERVICES[*]}
ExecStop=${COMPOSE_WRAPPER} stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${COMPOSE_SERVICE}"
  systemctl restart "${COMPOSE_SERVICE}"
}

# RAG の backend は Cookie session の login（AUTH_MODE=production）で UI と API を保護する。
# Nginx には認証を置かず、frontend の配信と /api/ の proxy（SSE のため buffering 無効）だけを行う。
configure_nginx() {
  log "Configuring Nginx on port ${APPLICATION_PORT}."
  cat > "${NGINX_SITES_AVAILABLE_DIR}/production-ready-rag" <<EOF
server {
    listen ${APPLICATION_PORT};
    server_name _;

    root ${FRONTEND_DIR}/dist;
    index index.html;

    access_log /var/log/nginx/production-ready-rag-access.log;
    error_log /var/log/nginx/production-ready-rag-error.log warn;

    client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;

    location = /api {
        return 308 /api/;
    }

    location /api/ {
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
    }

    location = /health {
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT}/api/health;
        proxy_set_header Host \$host;
        access_log off;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

  ln -sfn "${NGINX_SITES_AVAILABLE_DIR}/production-ready-rag" "${NGINX_SITES_ENABLED_DIR}/production-ready-rag"
  rm -f "${NGINX_SITES_ENABLED_DIR}/default"
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx
}

dump_compose_diagnostics() {
  log "Diagnostics: docker compose ps"
  "${COMPOSE_WRAPPER}" ps || true
  log "Diagnostics: backend logs"
  "${COMPOSE_WRAPPER}" logs --tail 160 backend || true
}

# /api/health は OCI / AI の設定前でも 200 を返す（/api/ready は設定が揃うまで 503）。
wait_for_backend() {
  local status=0
  log "Waiting for backend health endpoint."
  retry_command 30 curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" || status="$?"
  if [ "${status}" -eq 0 ]; then
    return 0
  fi
  log "Backend did not become healthy on ${BACKEND_HOST}:${BACKEND_PORT}."
  if [ "${DATABASE_INITIALIZATION_READY}" != "true" ]; then
    log "The RAG system schema was not initialized; check ADB reachability and ${BACKEND_DIR}/.env."
  fi
  dump_compose_diagnostics
  return "${status}"
}

main() {
  log "Starting Compute initialization (Docker Compose)."
  install_system_packages
  install_docker
  load_compose_services
  prepare_filesystem
  install_runtime_env
  write_compose_override
  build_images
  prepare_container_permissions
  initialize_database_schema
  build_frontend
  configure_systemd
  configure_nginx
  wait_for_backend
  log "Initialization complete. Open http://<compute-ip>/ and log in with the login user."
  log "Then configure OCI authentication and models in System settings."
}

if [ "${RAG_INIT_TEST_MODE:-false}" != "true" ]; then
  main "$@"
fi
