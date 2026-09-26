#!/usr/bin/env bash
# OCI Resource Manager の統合 stack（terraform/stack、#217）の cloud-init から呼ばれる agent の Compute 初期化スクリプト。
# Agent Control Plane を Docker なしで Nginx + systemd に直接配備する。
# ADB の DDL は持たない。Runtime 状態の table は backend 起動時に Oracle repository が作成する。
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [ "${AGENT_INIT_TEST_MODE:-false}" != "true" ]; then
  exec > >(tee -a /var/log/agent-init.log) 2>&1
fi

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
# Agent と共有 platform は monorepo（no.1-production-ready-suite）の agent/ と platform/ にある。
SUITE_REPO_DIR="${APP_ROOT}/no.1-production-ready-suite"
APP_REPO_DIR="${SUITE_REPO_DIR}/agent"
PLATFORM_REPO_DIR="${SUITE_REPO_DIR}/platform"
BACKEND_DIR="${APP_REPO_DIR}/backend"
FRONTEND_DIR="${APP_REPO_DIR}/frontend"
DATA_DIR="${DATA_DIR:-/u01/data/production-ready-agent}"
WALLET_DIR="${APP_ROOT}/wallet"
PROPS_DIR="${APP_ROOT}/props"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8020"
# checkpoint repository は process 内に状態を持つため、gunicorn は 1 worker に固定する。
BACKEND_WORKERS="1"
APPLICATION_PORT="${APPLICATION_PORT:-$(tr -d '[:space:]' < "${PROPS_DIR}/application_port.txt" 2>/dev/null || printf '80')}"
NODE_MAJOR="22"
NODESOURCE_KEYRING_PATH="${NODESOURCE_KEYRING_PATH:-/usr/share/keyrings/nodesource.gpg}"
NODESOURCE_SOURCE_PATH="${NODESOURCE_SOURCE_PATH:-/etc/apt/sources.list.d/nodesource.sources}"
NODESOURCE_KEY_URL="https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_REPO_URL="https://deb.nodesource.com/node_${NODE_MAJOR}.x"
NODEJS_OFFICIAL_RELEASE_BASE_URL="${NODEJS_OFFICIAL_RELEASE_BASE_URL:-https://nodejs.org/download/release/latest-v${NODE_MAJOR}.x}"
NODEJS_OFFICIAL_INSTALL_DIR="${NODEJS_OFFICIAL_INSTALL_DIR:-/usr/local/lib/nodejs}"
NODEJS_OFFICIAL_BIN_DIR="${NODEJS_OFFICIAL_BIN_DIR:-/usr/local/bin}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
NGINX_SITES_AVAILABLE_DIR="${NGINX_SITES_AVAILABLE_DIR:-/etc/nginx/sites-available}"
NGINX_SITES_ENABLED_DIR="${NGINX_SITES_ENABLED_DIR:-/etc/nginx/sites-enabled}"
NGINX_HTPASSWD_PATH="${NGINX_HTPASSWD_PATH:-/etc/nginx/production-ready-agent.htpasswd}"
NGINX_GROUP="${NGINX_GROUP:-www-data}"
OCI_IMDS_VNICS_URL="${OCI_IMDS_VNICS_URL:-http://169.254.169.254/opc/v2/vnics/}"
BACKEND_SERVICE="production-ready-agent-backend.service"
DATABASE_INITIALIZATION_READY=false

PATH="/usr/local/bin:/usr/bin:/bin:${PATH}"

log() {
  printf '[agent-init] %s\n' "$*"
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

run_as_app_user() {
  runuser -u "${APP_USER}" -- env PATH="${PATH}" "$@"
}

run_as_app_user_in_dir() {
  local workdir="$1"
  shift
  run_as_app_user bash -lc "cd '${workdir}' && $*"
}

install_system_packages() {
  log "Installing system packages."
  apt_get update
  apt_get install -y \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    nginx \
    netfilter-persistent \
    openssl \
    unzip \
    wget
}

# Node.js は NodeSource の apt repository を優先し、失敗時は公式 tarball（SHASUMS256 検証付き）へ fallback する。
# Agent の CI と同じ Node.js 22 系を使う。
install_nodejs() {
  local installed_version
  local npm_version

  if command -v node >/dev/null 2>&1; then
    installed_version="$(node --version)" || return 1
    if printf '%s\n' "${installed_version}" | grep -q "^v${NODE_MAJOR}\." && command -v npm >/dev/null 2>&1; then
      npm_version="$(npm --version)" || return 1
      log "Node.js ${installed_version} with npm ${npm_version} is already installed."
      return
    fi
  fi

  if install_nodejs_from_nodesource; then
    return
  fi

  log "NodeSource Node.js ${NODE_MAJOR} installation failed; falling back to official Node.js tarball."
  install_nodejs_from_official_tarball || return 1
}

install_nodejs_from_nodesource() {
  log "Installing Node.js ${NODE_MAJOR} from NodeSource."
  install_nodesource_apt_repository || return 1
  apt_get update || return 1
  ensure_nodejs_candidate || return 1
  apt_get install -y nodejs || return 1
  validate_nodejs || return 1
}

install_nodesource_apt_repository() {
  local architecture
  local keyring_tmp

  install -d -m 0755 "$(dirname "${NODESOURCE_KEYRING_PATH}")" "$(dirname "${NODESOURCE_SOURCE_PATH}")" || return 1
  keyring_tmp="$(mktemp)" || return 1
  if ! curl -fsSL "${NODESOURCE_KEY_URL}" | gpg --dearmor --yes -o "${keyring_tmp}"; then
    rm -f "${keyring_tmp}"
    return 1
  fi
  install -m 0644 "${keyring_tmp}" "${NODESOURCE_KEYRING_PATH}" || {
    rm -f "${keyring_tmp}"
    return 1
  }
  rm -f "${keyring_tmp}"
  architecture="$(dpkg --print-architecture)" || return 1

  if ! cat > "${NODESOURCE_SOURCE_PATH}" <<EOF
Types: deb
URIs: ${NODESOURCE_REPO_URL}
Suites: nodistro
Components: main
Architectures: ${architecture}
Signed-By: ${NODESOURCE_KEYRING_PATH}
EOF
  then
    return 1
  fi
  chmod 0644 "${NODESOURCE_SOURCE_PATH}" || return 1
}

ensure_nodejs_candidate() {
  local candidate
  if ! candidate="$(apt-cache policy nodejs | awk '/Candidate:/ {print $2; exit}')"; then
    log "Unable to inspect nodejs apt candidate."
    return 1
  fi
  if [ -z "${candidate}" ] || [ "${candidate}" = "(none)" ] || ! printf '%s\n' "${candidate}" | grep -q "^${NODE_MAJOR}\."; then
    log "NodeSource nodejs candidate is not Node.js ${NODE_MAJOR}: ${candidate:-<none>}"
    apt-cache policy nodejs || true
    return 1
  fi
}

resolve_nodejs_official_platform() {
  local architecture

  NODEJS_OFFICIAL_PLATFORM=""
  if ! command -v dpkg >/dev/null 2>&1 || ! architecture="$(dpkg --print-architecture)" || [ -z "${architecture}" ]; then
    architecture="$(uname -m)" || return 1
  fi

  case "${architecture}" in
    amd64 | x86_64)
      NODEJS_OFFICIAL_PLATFORM="linux-x64"
      ;;
    arm64 | aarch64)
      NODEJS_OFFICIAL_PLATFORM="linux-arm64"
      ;;
    *)
      log "Unsupported architecture for official Node.js tarball: ${architecture}"
      return 1
      ;;
  esac
}

install_nodejs_from_official_tarball() {
  local node_platform
  local node_dir
  local shasums_path
  local tarball_name
  local tmp_dir

  resolve_nodejs_official_platform || return 1
  node_platform="${NODEJS_OFFICIAL_PLATFORM}"
  tmp_dir="$(mktemp -d)" || return 1
  shasums_path="${tmp_dir}/SHASUMS256.txt"

  log "Installing Node.js ${NODE_MAJOR} official tarball for ${node_platform}."
  if ! curl -fsSL "${NODEJS_OFFICIAL_RELEASE_BASE_URL%/}/SHASUMS256.txt" -o "${shasums_path}"; then
    rm -rf "${tmp_dir}"
    return 1
  fi

  tarball_name="$(
    awk -v node_platform="${node_platform}" -v node_major="${NODE_MAJOR}" '
      {
        filename = $2
        sub("^[*]", "", filename)
        sub("^\\./", "", filename)
      }
      filename ~ ("^node-v" node_major "[.][0-9]+[.][0-9]+-" node_platform "[.]tar[.]gz$") {
        print filename
        exit
      }
    ' "${shasums_path}"
  )" || {
    rm -rf "${tmp_dir}"
    return 1
  }
  if [ -z "${tarball_name}" ]; then
    log "No official Node.js ${NODE_MAJOR} tarball found for ${node_platform}."
    rm -rf "${tmp_dir}"
    return 1
  fi

  if ! curl -fsSL "${NODEJS_OFFICIAL_RELEASE_BASE_URL%/}/${tarball_name}" -o "${tmp_dir}/${tarball_name}"; then
    rm -rf "${tmp_dir}"
    return 1
  fi
  if ! (cd "${tmp_dir}" && sha256sum --ignore-missing -c SHASUMS256.txt); then
    log "Official Node.js tarball checksum verification failed: ${tarball_name}"
    rm -rf "${tmp_dir}"
    return 1
  fi

  node_dir="${tarball_name%.tar.gz}"
  install -d -m 0755 "${NODEJS_OFFICIAL_INSTALL_DIR}" "${NODEJS_OFFICIAL_BIN_DIR}" || {
    rm -rf "${tmp_dir}"
    return 1
  }
  if ! tar -xzf "${tmp_dir}/${tarball_name}" -C "${NODEJS_OFFICIAL_INSTALL_DIR}"; then
    rm -rf "${tmp_dir}"
    return 1
  fi
  local executable
  for executable in node npm npx corepack; do
    if [ ! -x "${NODEJS_OFFICIAL_INSTALL_DIR}/${node_dir}/bin/${executable}" ]; then
      log "Official Node.js tarball is missing ${executable}."
      rm -rf "${tmp_dir}"
      return 1
    fi
    ln -sfn "${NODEJS_OFFICIAL_INSTALL_DIR}/${node_dir}/bin/${executable}" "${NODEJS_OFFICIAL_BIN_DIR}/${executable}" || {
      rm -rf "${tmp_dir}"
      return 1
    }
  done

  rm -rf "${tmp_dir}"
  hash -r || true
  validate_nodejs || return 1
}

validate_nodejs() {
  local installed_version
  local npm_version

  if ! command -v node >/dev/null 2>&1; then
    log "node command was not installed."
    return 1
  fi

  installed_version="$(node --version)" || return 1
  if ! printf '%s\n' "${installed_version}" | grep -q "^v${NODE_MAJOR}\."; then
    log "Expected Node.js v${NODE_MAJOR}.x after installation, got ${installed_version}."
    return 1
  fi

  if ! command -v npm >/dev/null 2>&1; then
    log "npm was not installed with Node.js ${installed_version}."
    return 1
  fi

  npm_version="$(npm --version)" || return 1
  log "Installed Node.js ${installed_version} with npm ${npm_version}."
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv $(uv --version) is already installed."
    return
  fi

  log "Installing uv."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  chmod 0755 /usr/local/bin/uv
  uv --version
}

prepare_filesystem() {
  log "Preparing application directories."
  if ! id "${APP_USER}" >/dev/null 2>&1; then
    log "Application user ${APP_USER} does not exist."
    return 1
  fi

  chown "root:${APP_GROUP}" "${APP_ROOT}"
  chmod 0775 "${APP_ROOT}"
  install -d -m 0755 -o "${APP_USER}" -g "${APP_GROUP}" \
    "${DATA_DIR}" "${DATA_DIR}/bindings" "${DATA_DIR}/artifacts"
  install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"
  chown -R "${APP_USER}:${APP_GROUP}" "${SUITE_REPO_DIR}" "${DATA_DIR}" "${WALLET_DIR}"
}

install_runtime_env() {
  log "Installing backend environment and wallet."
  install -m 0600 -o "${APP_USER}" -g "${APP_GROUP}" "${PROPS_DIR}/backend.env" "${BACKEND_DIR}/.env"
  resolve_public_base_url "${BACKEND_DIR}/.env"

  rm -rf "${WALLET_DIR}"
  install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"
  unzip -oq "${PROPS_DIR}/wallet.zip" -d "${WALLET_DIR}"
  chown -R "${APP_USER}:${APP_GROUP}" "${WALLET_DIR}"
  find "${WALLET_DIR}" -type d -exec chmod 0700 {} \;
  find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;
}

compute_private_ip() {
  local private_ip=""
  private_ip="$(
    curl -fsS --max-time 5 -H "Authorization: Bearer Oracle" "${OCI_IMDS_VNICS_URL}" 2>/dev/null \
      | grep -o '"privateIp"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -n 1 \
      | sed 's/.*"\([^"]*\)"$/\1/'
  )" || private_ip=""
  if [ -z "${private_ip}" ]; then
    private_ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || private_ip=""
  fi
  printf '%s\n' "${private_ip:-127.0.0.1}"
}

# AGENT_CONTROL_PLANE_PUBLIC_BASE_URL が空なら、Runtime から届く Compute の private IP で補う。
# 明示値（Resource Manager の入力）は変更しない。
resolve_public_base_url() {
  local env_file="$1"
  local current
  local base_url
  local private_ip

  current="$(sed -n 's/^AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=//p' "${env_file}" | tail -n 1)"
  if [ -n "${current}" ]; then
    log "AGENT_CONTROL_PLANE_PUBLIC_BASE_URL is configured explicitly."
    return 0
  fi

  private_ip="$(compute_private_ip)"
  if [ "${APPLICATION_PORT}" = "80" ]; then
    base_url="http://${private_ip}/api"
  else
    base_url="http://${private_ip}:${APPLICATION_PORT}/api"
  fi
  if grep -q '^AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=' "${env_file}"; then
    sed -i "s|^AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=.*$|AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=${base_url}|" "${env_file}"
  else
    printf 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=%s\n' "${base_url}" >> "${env_file}"
  fi
  log "AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=${base_url}"
}

install_backend() {
  log "Installing backend dependencies with uv."
  run_as_app_user_in_dir "${BACKEND_DIR}" "uv python install 3.12"
  run_as_app_user_in_dir "${BACKEND_DIR}" "uv sync --locked --no-dev --python 3.12"
}

# Runtime repository は import 時に Oracle へ接続し、自分の table を冪等に作成する
# （AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true）。ここでは起動前に一度作らせ、失敗理由を init log に残す。
initialize_database_schema() {
  log "Initializing Agent Runtime Oracle repository (idempotent)."
  if retry_command 5 run_as_app_user_in_dir "${BACKEND_DIR}" \
    "uv run python -c 'import app.features.agent.runtime'"; then
    DATABASE_INITIALIZATION_READY=true
    log "Agent Runtime Oracle repository is ready."
    return 0
  fi

  DATABASE_INITIALIZATION_READY=false
  log "WARNING: Agent Runtime Oracle repository initialization failed."
  log "WARNING: The backend cannot start until ADB is reachable; systemd keeps retrying."
  log "Recovery: cd ${BACKEND_DIR} && sudo -u ${APP_USER} /usr/local/bin/uv run python -c 'import app.features.agent.runtime'"
  log "Recovery: sudo systemctl restart ${BACKEND_SERVICE}"
  return 0
}

build_frontend() {
  log "Building shared UI package."
  run_as_app_user_in_dir "${PLATFORM_REPO_DIR}" "npm ci"
  run_as_app_user_in_dir "${PLATFORM_REPO_DIR}" "npm run build"

  log "Building Agent frontend."
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm ci"
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm run build"
}

configure_systemd() {
  log "Writing systemd unit."
  cat > "${SYSTEMD_UNIT_DIR}/${BACKEND_SERVICE}" <<EOF
[Unit]
Description=Production Ready Agent Control Plane backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${BACKEND_DIR}
Environment=HOME=/home/${APP_USER}
Environment=PYTHONUNBUFFERED=1
Environment=UV_NO_PROGRESS=1
ExecStart=/usr/local/bin/uv run gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind ${BACKEND_HOST}:${BACKEND_PORT} --workers ${BACKEND_WORKERS} --timeout 300
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${BACKEND_SERVICE}"
  systemctl restart "${BACKEND_SERVICE}"
}

# Agent には組み込みの login がないため、UI と API 全体を Nginx の Basic 認証で保護する。
# パスワードは SHA-512 crypt の hash だけを Nginx の password file に保存する。
configure_basic_auth() {
  local user
  local hash
  local tmp_file

  user="$(tr -d '[:space:]' < "${PROPS_DIR}/basic_auth_user.txt")"
  if [ -z "${user}" ] || [ ! -s "${PROPS_DIR}/basic_auth_password" ]; then
    log "Basic authentication user or password is missing in ${PROPS_DIR}."
    return 1
  fi

  log "Configuring Nginx Basic authentication for ${user}."
  hash="$(openssl passwd -6 -stdin < "${PROPS_DIR}/basic_auth_password")"
  tmp_file="$(mktemp "${NGINX_HTPASSWD_PATH}.XXXXXX")"
  printf '%s:%s\n' "${user}" "${hash}" > "${tmp_file}"
  chgrp "${NGINX_GROUP}" "${tmp_file}"
  chmod 0640 "${tmp_file}"
  mv -f "${tmp_file}" "${NGINX_HTPASSWD_PATH}"
}

configure_nginx() {
  log "Configuring Nginx on port ${APPLICATION_PORT}."
  cat > "${NGINX_SITES_AVAILABLE_DIR}/production-ready-agent" <<EOF
server {
    listen ${APPLICATION_PORT};
    server_name _;

    root ${FRONTEND_DIR}/dist;
    index index.html;

    access_log /var/log/nginx/production-ready-agent-access.log;
    error_log /var/log/nginx/production-ready-agent-error.log warn;

    client_max_body_size 100M;
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;

    auth_basic "Production Ready Agent";
    auth_basic_user_file ${NGINX_HTPASSWD_PATH};

    location = /api {
        return 308 /api/;
    }

    # Binding MCP endpoint は Binding 固有 token で認証する（Runtime からの呼出し境界）。
    location /api/mcp/ {
        auth_basic off;
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
    }

    location /api/ {
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_cache off;
    }

    location = /health {
        auth_basic off;
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT}/api/health;
        proxy_set_header Host \$host;
        access_log off;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

  ln -sfn "${NGINX_SITES_AVAILABLE_DIR}/production-ready-agent" "${NGINX_SITES_ENABLED_DIR}/production-ready-agent"
  rm -f "${NGINX_SITES_ENABLED_DIR}/default"
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx
}

dump_service_diagnostics() {
  local service="$1"
  log "Diagnostics for ${service}: systemctl status"
  systemctl --no-pager --full status "${service}" || true
  log "Diagnostics for ${service}: recent journal"
  journalctl -u "${service}" -n 160 --no-pager || true
}

wait_for_backend() {
  log "Waiting for backend health endpoint."
  if retry_command 30 curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health"; then
    return 0
  fi
  local status="$?"
  log "Backend did not become healthy on ${BACKEND_HOST}:${BACKEND_PORT}."
  if [ "${DATABASE_INITIALIZATION_READY}" != "true" ]; then
    log "The Agent Runtime Oracle repository was not ready; check ADB reachability and backend/.env."
  fi
  dump_service_diagnostics "${BACKEND_SERVICE}"
  return "${status}"
}

main() {
  log "Starting direct Compute initialization."
  install_system_packages
  install_nodejs
  install_uv
  prepare_filesystem
  install_runtime_env
  install_backend
  initialize_database_schema
  build_frontend
  configure_systemd
  configure_basic_auth
  configure_nginx
  wait_for_backend
  log "Initialization complete. Open http://<compute-ip>/ with the Basic authentication user."
}

if [ "${AGENT_INIT_TEST_MODE:-false}" != "true" ]; then
  main "$@"
fi
