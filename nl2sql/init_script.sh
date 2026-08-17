#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

exec > >(tee -a /var/log/nl2sql-init.log) 2>&1

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
APP_USER="${APP_USER:-ubuntu}"
APP_REPO_DIR="${APP_ROOT}/no.1-production-ready-nl2sql"
PLATFORM_REPO_DIR="${APP_ROOT}/no.1-production-ready-platform"
BACKEND_DIR="${APP_REPO_DIR}/backend"
FRONTEND_DIR="${APP_REPO_DIR}/frontend"
DATA_DIR="/u01/production-ready-nl2sql"
WALLET_DIR="${APP_ROOT}/wallet"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
APPLICATION_PORT="${APPLICATION_PORT:-$(tr -d '[:space:]' < "${APP_ROOT}/props/application_port.txt" 2>/dev/null || printf '80')}"

PATH="/usr/local/bin:/usr/bin:/bin:${PATH}"

log() {
  printf '[nl2sql-init] %s\n' "$*"
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
    unzip \
    wget
}

install_nodejs() {
  if command -v node >/dev/null 2>&1 && node --version | grep -q '^v22\.'; then
    log "Node.js $(node --version) is already installed."
    return
  fi

  log "Installing Node.js 22."
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt_get install -y nodejs
  node --version
  npm --version
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

  install -d -m 0755 -o "${APP_USER}" -g "${APP_USER}" "${DATA_DIR}"
  install -d -m 0700 -o "${APP_USER}" -g "${APP_USER}" "${WALLET_DIR}"
  chown -R "${APP_USER}:${APP_USER}" "${APP_REPO_DIR}" "${PLATFORM_REPO_DIR}" "${DATA_DIR}" "${WALLET_DIR}"
}

install_runtime_env() {
  log "Installing backend environment and wallet."
  install -m 0600 -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/props/backend.env" "${BACKEND_DIR}/.env"

  rm -rf "${WALLET_DIR}"
  install -d -m 0700 -o "${APP_USER}" -g "${APP_USER}" "${WALLET_DIR}"
  unzip -oq "${APP_ROOT}/props/wallet.zip" -d "${WALLET_DIR}"
  chown -R "${APP_USER}:${APP_USER}" "${WALLET_DIR}"
  find "${WALLET_DIR}" -type d -exec chmod 0700 {} \;
  find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;
}

install_backend() {
  log "Installing backend dependencies with uv."
  run_as_app_user_in_dir "${BACKEND_DIR}" "uv python install 3.12"
  run_as_app_user_in_dir "${BACKEND_DIR}" "uv sync --locked --no-dev --python 3.12"
}

build_frontend() {
  log "Building shared UI package."
  run_as_app_user_in_dir "${PLATFORM_REPO_DIR}" "npm ci"
  run_as_app_user_in_dir "${PLATFORM_REPO_DIR}" "npm run build --workspace @engchina/production-ready-ui"

  log "Building NL2SQL frontend."
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm ci"
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm run build"
}

initialize_database_schema() {
  log "Initializing NL2SQL system schema."
  retry_command 10 run_as_app_user_in_dir "${BACKEND_DIR}" "uv run python -m app.cli.nl2sql_system_schema --initialize"
}

write_systemd_unit() {
  local unit_path="$1"
  local exec_start="$2"
  local description="$3"

  cat > "${unit_path}" <<EOF
[Unit]
Description=${description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${BACKEND_DIR}
Environment=HOME=/home/${APP_USER}
Environment=PYTHONUNBUFFERED=1
Environment=UV_NO_PROGRESS=1
ExecStart=${exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

configure_systemd() {
  log "Writing systemd units."
  write_systemd_unit \
    /etc/systemd/system/production-ready-nl2sql-backend.service \
    "/usr/local/bin/uv run gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind ${BACKEND_HOST}:${BACKEND_PORT} --workers 2 --timeout 300" \
    "Production Ready NL2SQL backend"

  write_systemd_unit \
    /etc/systemd/system/production-ready-nl2sql-schema-refresh-worker.service \
    "/usr/local/bin/uv run python -m app.cli.nl2sql_schema_refresh_worker --poll-seconds 1" \
    "Production Ready NL2SQL schema refresh worker"

  write_systemd_unit \
    /etc/systemd/system/production-ready-nl2sql-quality-evaluation-worker.service \
    "/usr/local/bin/uv run python -m app.cli.nl2sql_quality_evaluation_worker" \
    "Production Ready NL2SQL quality evaluation worker"

  write_systemd_unit \
    /etc/systemd/system/production-ready-nl2sql-ontology-worker.service \
    "/usr/local/bin/uv run python -m app.features.nl2sql.ontology_worker" \
    "Production Ready NL2SQL ontology worker"

  systemctl daemon-reload
  systemctl enable \
    production-ready-nl2sql-backend.service \
    production-ready-nl2sql-schema-refresh-worker.service \
    production-ready-nl2sql-quality-evaluation-worker.service \
    production-ready-nl2sql-ontology-worker.service
  systemctl restart \
    production-ready-nl2sql-backend.service \
    production-ready-nl2sql-schema-refresh-worker.service \
    production-ready-nl2sql-quality-evaluation-worker.service \
    production-ready-nl2sql-ontology-worker.service
}

configure_nginx() {
  log "Configuring Nginx on port ${APPLICATION_PORT}."
  cat > /etc/nginx/sites-available/production-ready-nl2sql <<EOF
server {
    listen ${APPLICATION_PORT};
    server_name _;

    root ${FRONTEND_DIR}/dist;
    index index.html;

    access_log /var/log/nginx/production-ready-nl2sql-access.log;
    error_log /var/log/nginx/production-ready-nl2sql-error.log warn;

    client_max_body_size 200M;
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
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_cache off;
    }

    location = /health {
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT}/health;
        proxy_set_header Host \$host;
        access_log off;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

  ln -sfn /etc/nginx/sites-available/production-ready-nl2sql /etc/nginx/sites-enabled/production-ready-nl2sql
  rm -f /etc/nginx/sites-enabled/default
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
  if retry_command 30 curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/health"; then
    return 0
  fi
  local status="$?"
  log "Backend did not become healthy on ${BACKEND_HOST}:${BACKEND_PORT}."
  dump_service_diagnostics production-ready-nl2sql-backend.service
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
  build_frontend
  initialize_database_schema
  configure_systemd
  configure_nginx
  wait_for_backend
  log "Initialization complete. Open http://<compute-ip>/"
}

main "$@"
