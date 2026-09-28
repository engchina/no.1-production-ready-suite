#!/usr/bin/env bash
# OCI Resource Manager の統合 stack（terraform/stack、#217）の cloud-init から呼ばれる rag の Compute 初期化スクリプト。
# RAG は Docker を使わず、NL2SQL / Agent と同じくネイティブ（uv の venv + systemd + Nginx）で動かす（#286）。
#   - backend / ingestion-worker: rag/backend の venv。backend は 127.0.0.1:8000 だけで listen する
#   - 前処理 / CPU parser: サービスごとの venv（rag/services/*/*/.venv）と systemd の unit。127.0.0.1:<port>
#     （unit の定義は rag/scripts/rag-systemd.sh。開発環境の rag/scripts/rag-services.sh と共通）
#   - frontend: host の Node.js で静的 build し、Nginx が配信して /api/ を backend へ proxy する
# アプリのプロセスは専用のユーザー（SERVICE_USER）で動かし、backend にはサービス管理画面から操作する
# unit の systemctl / journalctl だけを sudoers で許可する。
# 前処理 / parser の起動 / 停止は利用者が最後に操作した状態（systemd の enable / disable）を保ち、
# 再配備・再起動でもその状態に戻す。初めて配備する unit は起動する。
# ADB の DDL は持たない。RAG の system schema はアプリの CLI（app.rag.system_schema_cli）で適用する。
# GPU の service（ASR）は扱わない。
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [ "${RAG_INIT_TEST_MODE:-false}" != "true" ]; then
  exec > >(tee -a /var/log/rag-init.log) 2>&1
fi

INIT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=scripts/rag-systemd.sh
source "${INIT_SCRIPT_DIR}/scripts/rag-systemd.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
# リポジトリの所有者（git pull・uv sync・frontend の build を行う）。
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
# RAG のプロセスを動かす専用のユーザー。APP_USER（OCI の Ubuntu では sudo を無制限に持つ）とは分け、
# sudo は sudoers で許可した unit の操作だけにする。
SERVICE_USER="${SERVICE_USER:-ragsvc}"
SERVICE_GROUP="${SERVICE_GROUP:-${SERVICE_USER}}"
SERVICE_HOME="${SERVICE_HOME:-/var/lib/production-ready-rag}"
# RAG と共有 platform は monorepo（no.1-production-ready-suite）の rag/ と platform/ にある。
SUITE_REPO_DIR="${APP_ROOT}/no.1-production-ready-suite"
APP_REPO_DIR="${SUITE_REPO_DIR}/rag"
BACKEND_DIR="${APP_REPO_DIR}/backend"
# 3製品共通の設定（PLATFORM_*）の共通 .env（#211）。システム設定画面の保存先（model-settings.json も同じ場所）。
PLATFORM_DIR="${SUITE_REPO_DIR}/platform"
PLATFORM_ENV_FILE="${PLATFORM_DIR}/.env"
FRONTEND_DIR="${APP_REPO_DIR}/frontend"
# アップロード原本の保存先（backend の PLATFORM_LOCAL_STORAGE_DIR の既定値）。
DATA_DIR="${DATA_DIR:-/u01/data/production-ready-rag}"
# サービス管理が起動 / 再起動の前に書く、マイクロサービスの実行用 env（backend の RAG_SERVICE_RUNTIME_ENV_FILE の既定値）。
SERVICE_RUNTIME_ENV_FILE="${BACKEND_DIR}/service-runtime.env"
WALLET_DIR="${APP_ROOT}/wallet"
PROPS_DIR="${APP_ROOT}/props"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
APPLICATION_PORT="${APPLICATION_PORT:-$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/application_port.txt" || printf '80')}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
SUDOERS_DIR="${SUDOERS_DIR:-/etc/sudoers.d}"
NGINX_SITES_AVAILABLE_DIR="${NGINX_SITES_AVAILABLE_DIR:-/etc/nginx/sites-available}"
NGINX_SITES_ENABLED_DIR="${NGINX_SITES_ENABLED_DIR:-/etc/nginx/sites-enabled}"
# uv が入れる Python は、SERVICE_USER からも読める共有の場所に置く（APP_USER の home は他のユーザーが読めない）。
UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}"
BACKEND_UNIT="production-ready-rag-backend.service"
WORKER_UNIT="production-ready-rag-ingestion-worker.service"
# 取込 worker の停止: SIGTERM を受けたら実行中の job を grace まで待ち、終わらなければ子を止めて
# （SIGTERM から 10 秒で SIGKILL）自分の lease の job を QUEUED に戻す（#357）。systemd の
# TimeoutStopSec は grace + 子の停止待ち + 戻す DB の処理より長くする（テストで照合する）。
WORKER_SHUTDOWN_GRACE_SECONDS=60
WORKER_TIMEOUT_STOP_SEC=90
# backend の RAG_MAX_UPLOAD_BYTES の既定値（rag/backend/app/config.py の max_upload_bytes。テストで照合する）。
RAG_DEFAULT_MAX_UPLOAD_BYTES=209715200
# Nginx の client_max_body_size は backend の上限に multipart の境界・header の余白を足す（Refs #306）。
NGINX_UPLOAD_MARGIN_MIB=10
NODESOURCE_KEYRING_PATH="${NODESOURCE_KEYRING_PATH:-/usr/share/keyrings/nodesource.gpg}"
NODESOURCE_SOURCE_PATH="${NODESOURCE_SOURCE_PATH:-/etc/apt/sources.list.d/nodesource.sources}"
NODESOURCE_KEY_URL="https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_REPO_URL="https://deb.nodesource.com/node_24.x"
NODEJS_OFFICIAL_RELEASE_BASE_URL="${NODEJS_OFFICIAL_RELEASE_BASE_URL:-https://nodejs.org/download/release/latest-v24.x}"
NODEJS_OFFICIAL_INSTALL_DIR="${NODEJS_OFFICIAL_INSTALL_DIR:-/usr/local/lib/nodejs}"
NODEJS_OFFICIAL_BIN_DIR="${NODEJS_OFFICIAL_BIN_DIR:-/usr/local/bin}"
# 以前の Docker Compose の配備（#286 より前）。見つかれば止めて、データを移す。
LEGACY_COMPOSE_UNIT="production-ready-rag.service"
LEGACY_COMPOSE_WRAPPER="${LEGACY_COMPOSE_WRAPPER:-/usr/local/bin/rag-compose}"
LEGACY_COMPOSE_PROJECT="production-ready-rag"
DOCKER_VOLUMES_DIR="${DOCKER_VOLUMES_DIR:-/var/lib/docker/volumes}"
DATABASE_INITIALIZATION_READY=false

# この stack が配備してよい前処理 / parser（CPU / OCI だけ）。GPU の service はここに含めない。
# rag_services.txt にこれ以外の名前があれば失敗させる。
ALLOWED_RAG_SERVICES=(
  preprocess-office-to-pdf
  preprocess-pdf-to-page-images
  preprocess-csv-to-json
  preprocess-excel-to-json
  preprocess-url-to-markdown
  preprocess-image-enhance
  preprocess-pii-redact
  parser-docling
  parser-unstructured
  parser-oci-genai-vision
  parser-oci-document-understanding
)
# 既定の解析エンジン（Docling）の parser は必ず配備する。
REQUIRED_RAG_SERVICES=(parser-docling)
RAG_SERVICES=()

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

run_as_app_user() {
  runuser -u "${APP_USER}" -- env PATH="${PATH}" UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" "$@"
}

run_as_app_user_in_dir() {
  local workdir="$1"
  shift
  run_as_app_user bash -lc "cd '${workdir}' && $*"
}

run_as_service_user_in_dir() {
  local workdir="$1"
  shift
  runuser -u "${SERVICE_USER}" -- env PATH="${PATH}" HOME="${SERVICE_HOME}" \
    bash -c "cd '${workdir}' && $*"
}

contains_value() {
  local needle="$1"
  shift
  local value
  for value in "$@"; do
    if [ "${value}" = "${needle}" ]; then
      return 0
    fi
  done
  return 1
}

# 配備する前処理 / parser（Terraform の rag_services）。以前の stack の compose_services.txt も読む
# （backend / ingestion-worker は常に配備するため、その名前は読み飛ばす）。
load_rag_services() {
  local services_file="${PROPS_DIR}/rag_services.txt"
  local names=()
  local name

  if [ ! -f "${services_file}" ] && [ -f "${PROPS_DIR}/compose_services.txt" ]; then
    log "Reading the service list written by an older stack: ${PROPS_DIR}/compose_services.txt"
    services_file="${PROPS_DIR}/compose_services.txt"
  fi
  read -r -a names <<< "$(tr '\n' ' ' < "${services_file}" 2>/dev/null || true)"
  RAG_SERVICES=()
  for name in "${names[@]}"; do
    case "${name}" in
      backend | ingestion-worker)
        continue
        ;;
    esac
    if ! contains_value "${name}" "${ALLOWED_RAG_SERVICES[@]}"; then
      log "RAG service is not allowed by this stack: ${name}"
      return 1
    fi
    if ! contains_value "${name}" "${RAG_SERVICES[@]}"; then
      RAG_SERVICES+=("${name}")
    fi
  done
  for name in "${REQUIRED_RAG_SERVICES[@]}"; do
    if ! contains_value "${name}" "${RAG_SERVICES[@]}"; then
      log "Required RAG service is missing from ${services_file}: ${name}"
      return 1
    fi
  done
  log "RAG services: ${RAG_SERVICES[*]}"
}

# 前処理 / parser が使う OS のコマンド（LibreOffice・poppler・tesseract・CJK フォント）。
service_os_packages() {
  case "$1" in
    preprocess-office-to-pdf)
      printf '%s\n' libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc fonts-noto-cjk
      ;;
    preprocess-pdf-to-page-images)
      printf '%s\n' fonts-noto-cjk
      ;;
    parser-unstructured)
      printf '%s\n' poppler-utils tesseract-ocr tesseract-ocr-jpn
      ;;
  esac
}

install_system_packages() {
  local packages=(
    build-essential
    ca-certificates
    curl
    git
    gnupg
    nginx
    netfilter-persistent
    openssl
    sudo
    unzip
    wget
  )
  local service package
  for service in "${RAG_SERVICES[@]}"; do
    while IFS= read -r package; do
      if [ -n "${package}" ] && ! contains_value "${package}" "${packages[@]}"; then
        packages+=("${package}")
      fi
    done < <(service_os_packages "${service}")
  done
  log "Installing system packages: ${packages[*]}"
  apt_get update
  apt_get install -y --no-install-recommends "${packages[@]}"
}

install_nodejs() {
  local installed_version
  local npm_version

  if command -v node >/dev/null 2>&1; then
    installed_version="$(node --version)" || return 1
    if printf '%s\n' "${installed_version}" | grep -q '^v24\.' && command -v npm >/dev/null 2>&1; then
      npm_version="$(npm --version)" || return 1
      log "Node.js ${installed_version} with npm ${npm_version} is already installed."
      return
    fi
  fi

  if install_nodejs_from_nodesource; then
    return
  fi

  log "NodeSource Node.js 24 installation failed; falling back to official Node.js tarball."
  install_nodejs_from_official_tarball || return 1
}

install_nodejs_from_nodesource() {
  log "Installing Node.js 24 from NodeSource."
  install_nodesource_apt_repository || return 1
  apt_get update || return 1
  ensure_nodejs_24_candidate || return 1
  apt_get install -y nodejs || return 1
  validate_nodejs_24 || return 1
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

ensure_nodejs_24_candidate() {
  local candidate
  if ! candidate="$(apt-cache policy nodejs | awk '/Candidate:/ {print $2; exit}')"; then
    log "Unable to inspect nodejs apt candidate."
    return 1
  fi
  if [ -z "${candidate}" ] || [ "${candidate}" = "(none)" ] || ! printf '%s\n' "${candidate}" | grep -q '^24\.'; then
    log "NodeSource nodejs candidate is not Node.js 24: ${candidate:-<none>}"
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

  log "Installing Node.js 24 official tarball for ${node_platform}."
  if ! curl -fsSL "${NODEJS_OFFICIAL_RELEASE_BASE_URL%/}/SHASUMS256.txt" -o "${shasums_path}"; then
    rm -rf "${tmp_dir}"
    return 1
  fi

  tarball_name="$(
    awk -v node_platform="${node_platform}" '
      {
        filename = $2
        sub("^[*]", "", filename)
        sub("^\\./", "", filename)
      }
      filename ~ ("^node-v24[.][0-9]+[.][0-9]+-" node_platform "[.]tar[.]gz$") {
        print filename
        exit
      }
    ' "${shasums_path}"
  )" || {
    rm -rf "${tmp_dir}"
    return 1
  }
  if [ -z "${tarball_name}" ]; then
    log "No official Node.js 24 tarball found for ${node_platform}."
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
  if ! symlink_official_nodejs_binaries "${NODEJS_OFFICIAL_INSTALL_DIR}/${node_dir}"; then
    rm -rf "${tmp_dir}"
    return 1
  fi

  rm -rf "${tmp_dir}"
  hash -r || true
  validate_nodejs_24 || return 1
}

symlink_official_nodejs_binaries() {
  local executable
  local node_home="$1"

  for executable in node npm npx corepack; do
    if [ ! -x "${node_home}/bin/${executable}" ]; then
      log "Official Node.js tarball is missing ${executable}: ${node_home}/bin/${executable}"
      return 1
    fi
    ln -sfn "${node_home}/bin/${executable}" "${NODEJS_OFFICIAL_BIN_DIR}/${executable}" || return 1
  done
}

validate_nodejs_24() {
  local installed_version
  local npm_version

  if ! command -v node >/dev/null 2>&1; then
    log "node command was not installed."
    return 1
  fi

  installed_version="$(node --version)" || return 1
  if ! printf '%s\n' "${installed_version}" | grep -q '^v24\.'; then
    log "Expected Node.js v24.x after installation, got ${installed_version}."
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
  else
    log "Installing uv."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    chmod 0755 /usr/local/bin/uv
    uv --version
  fi
  # 全サービスの venv が使う Python 3.12 を、SERVICE_USER も読める共有の場所に入れる。
  install -d -m 0755 "${UV_PYTHON_INSTALL_DIR}"
  retry_command 3 env UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" uv python install "${RAG_PYTHON_VERSION}"
  chmod -R a+rX "${UV_PYTHON_INSTALL_DIR}"
}

# RAG のプロセスを動かす専用のユーザー（login できない system user）。
prepare_service_user() {
  if id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "Service user ${SERVICE_USER} already exists."
  else
    log "Creating service user ${SERVICE_USER}."
    useradd --system --user-group --create-home --home-dir "${SERVICE_HOME}" \
      --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi
  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${SERVICE_HOME}"
}

prepare_filesystem() {
  log "Preparing application directories."
  if ! id "${APP_USER}" >/dev/null 2>&1; then
    log "Application user ${APP_USER} does not exist."
    return 1
  fi

  chown "root:${APP_GROUP}" "${APP_ROOT}"
  chmod 0775 "${APP_ROOT}"
  install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DATA_DIR}"
  # git pull・uv sync・frontend の build は APP_USER が行う。
  chown -R "${APP_USER}:${APP_GROUP}" "${SUITE_REPO_DIR}"
  # 画面から保存する .env（backend/.env・platform/.env・model-settings.json）は、同じディレクトリの
  # 一時ファイルと lock で置き換えるため、SERVICE_USER が backend/ と platform/ に書けるようにする。
  chgrp "${SERVICE_GROUP}" "${BACKEND_DIR}" "${PLATFORM_DIR}"
  chmod 2775 "${BACKEND_DIR}" "${PLATFORM_DIR}"
  restore_service_owned_files
}

# SERVICE_USER が書くファイル（画面の保存先・サービス実行用 env）の所有者を戻す（chown -R の後）。
restore_service_owned_files() {
  local path
  for path in \
    "${PLATFORM_ENV_FILE}" \
    "${PLATFORM_DIR}/model-settings.json" \
    "${PLATFORM_DIR}/model-settings.json.lock" \
    "${BACKEND_DIR}/.env" \
    "${SERVICE_RUNTIME_ENV_FILE}"; do
    if [ -e "${path}" ]; then
      chown "${SERVICE_USER}:${SERVICE_GROUP}" "${path}"
    fi
  done
}

# 以前の Docker Compose の配備（production-ready-rag.service と rag-compose）を止め、コンテナの
# named volume に残したデータ（アップロード原本・OCI の設定）を host の場所へ移す（#286）。
# Docker 自体と image / volume は消さない（移行を確かめた後に利用者が消す。rag/docs/deployment.md）。
migrate_from_docker_compose() {
  local legacy_unit_path="${SYSTEMD_UNIT_DIR:?}/${LEGACY_COMPOSE_UNIT:?}"
  if [ -f "${legacy_unit_path}" ]; then
    log "Stopping the previous Docker Compose deployment (${LEGACY_COMPOSE_UNIT})."
    systemctl disable --now "${LEGACY_COMPOSE_UNIT}" || true
    rm -f -- "${legacy_unit_path}"
    systemctl daemon-reload
  fi
  if [ -x "${LEGACY_COMPOSE_WRAPPER}" ]; then
    # restart policy 付きのコンテナが port 8000 を掴んだまま残らないよう、コンテナを消す（volume は残す）。
    "${LEGACY_COMPOSE_WRAPPER}" down --remove-orphans || true
    mv -f "${LEGACY_COMPOSE_WRAPPER}" "${LEGACY_COMPOSE_WRAPPER}.legacy-docker" || true
  fi
  copy_legacy_volume "${LEGACY_COMPOSE_PROJECT}_backend-local-storage" "${DATA_DIR}"
  copy_legacy_volume "${LEGACY_COMPOSE_PROJECT}_oci-config" "${SERVICE_HOME}/.oci"
}

copy_legacy_volume() {
  local volume="$1"
  local dest="$2"
  local source_dir="${DOCKER_VOLUMES_DIR}/${volume}/_data"

  if [ ! -d "${source_dir}" ]; then
    return 0
  fi
  if [ -d "${dest}" ] && find "${dest}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    log "Keeping ${dest}; it already has data (legacy Docker volume ${volume} is left as is)."
    return 0
  fi
  log "Copying legacy Docker volume ${volume} to ${dest}."
  install -d -m 0700 "${dest}"
  cp -a "${source_dir}/." "${dest}/"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${dest}"
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

# rag/backend/.env（RAG_*）は Resource Manager の入力から作り、空の RAG_AUDIT_CONTEXT_HASH_SALT だけを
# instance 上で生成した値で補う。platform/.env（PLATFORM_*、3製品共通）はシステム設定画面の保存先でもあるため、
# 既にあれば上書きしない（画面で保存した API key などを再実行で消さない）。どちらも SERVICE_USER だけが読む。
install_runtime_env() {
  local env_file="${BACKEND_DIR}/.env"
  local current

  if [ -e "${PLATFORM_ENV_FILE}" ]; then
    log "Keeping existing shared platform environment: ${PLATFORM_ENV_FILE}"
  else
    log "Installing shared platform environment."
    install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${PROPS_DIR}/platform.env" "${PLATFORM_ENV_FILE}"
  fi
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${PLATFORM_ENV_FILE}"
  chmod 0600 "${PLATFORM_ENV_FILE}"

  log "Installing backend environment."
  install -m 0600 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${PROPS_DIR}/backend.env" "${env_file}"

  current="$(sed -n 's/^RAG_AUDIT_CONTEXT_HASH_SALT=//p' "${env_file}" | tail -n 1)"
  if [ -z "${current}" ]; then
    set_env_value "${env_file}" RAG_AUDIT_CONTEXT_HASH_SALT "$(ensure_generated_secret "${PROPS_DIR}/audit_context_hash_salt")"
  fi
}

install_wallet() {
  log "Installing wallet for ${SERVICE_USER}."
  rm -rf -- "${WALLET_DIR:?}"
  install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${WALLET_DIR}"
  unzip -oq "${PROPS_DIR}/wallet.zip" -d "${WALLET_DIR}"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${WALLET_DIR}"
  find "${WALLET_DIR}" -type d -exec chmod 0700 {} \;
  find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;
}

# backend と前処理 / parser の venv をサービスごとに作る（uv の lock どおり。Python 3.12）。
install_service_venvs() {
  local service dir extra model_command
  local sync_args=()

  log "Installing backend dependencies with uv."
  retry_command 3 run_as_app_user_in_dir "${BACKEND_DIR}" "uv sync --locked --no-dev --python ${RAG_PYTHON_VERSION}"

  for service in "${RAG_SERVICES[@]}"; do
    dir="${APP_REPO_DIR}/$(rag_service_dir "${service}")"
    mapfile -t sync_args < <(rag_uv_sync_args "${service}")
    log "Installing ${service} dependencies with uv."
    retry_command 3 run_as_app_user_in_dir "${dir}" "uv ${sync_args[*]}"
    extra="$(rag_service_venv_extra_command "${service}")"
    if [ -n "${extra}" ]; then
      retry_command 3 run_as_app_user_in_dir "${dir}" "${extra}"
    fi
    model_command="$(rag_service_model_command "${service}")"
    if [ -n "${model_command}" ] && ! retry_command 3 run_as_service_user_in_dir "${dir}" "${model_command}"; then
      log "WARNING: ${service} could not download its models now; it will download them on first use."
    fi
  done
}

# RAG の system schema（table / vector index / Oracle Text）はアプリの CLI が冪等に作成・更新する。
# 失敗しても backend は起動でき、画面（システム設定 > データベース > RAG システムテーブル）からも初期化できる。
initialize_database_schema() {
  log "Initializing RAG system schema (idempotent)."
  if retry_command 5 run_as_service_user_in_dir "${BACKEND_DIR}" \
    ".venv/bin/python -m app.rag.system_schema_cli initialize"; then
    DATABASE_INITIALIZATION_READY=true
    log "RAG system schema is ready."
    return 0
  fi

  DATABASE_INITIALIZATION_READY=false
  log "WARNING: RAG system schema initialization failed. Check ADB reachability and ${PLATFORM_ENV_FILE}."
  log "Recovery: cd ${BACKEND_DIR} && sudo -u ${SERVICE_USER} HOME=${SERVICE_HOME} .venv/bin/python -m app.rag.system_schema_cli initialize"
  log "Recovery: or open System settings > Database > RAG system tables in the application."
  return 0
}

# frontend は file:../../platform/packages/ui に依存するため、platform を先に build する。
build_frontend() {
  log "Building shared UI package."
  run_as_app_user_in_dir "${PLATFORM_DIR}" "npm ci"
  run_as_app_user_in_dir "${PLATFORM_DIR}" "npm run build"

  log "Building RAG frontend."
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm ci"
  run_as_app_user_in_dir "${FRONTEND_DIR}" "npm run build"
  test -f "${FRONTEND_DIR}/dist/index.html"
}

# backend と ingestion-worker の unit。RAG_ENVIRONMENT などは以前の Docker Compose の配備（#286 より前）の environment と同じ値。
write_backend_unit() {
  local unit_path="$1"
  local description="$2"
  local exec_start="$3"
  local extra="$4"

  cat > "${unit_path}" <<EOF
# rag/init_script.sh が生成する（手で編集しない）。#286
[Unit]
Description=${description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${BACKEND_DIR}
Environment=HOME=${SERVICE_HOME}
Environment=PYTHONUNBUFFERED=1
Environment=RAG_ENVIRONMENT=prod
Environment=RAG_INGESTION_QUEUE_DEDICATED_WORKER_ENABLED=true
Environment=RAG_PARSER_SERVICE_TIMEOUT_SECONDS=1230
Environment=RAG_PREPROCESS_ENABLED=true
${extra}ExecStart=${exec_start}

[Install]
WantedBy=multi-user.target
EOF
}

# 前処理 / parser の unit の起動 / 停止は、利用者が最後に操作した状態（systemd の enable / disable）を保つ。
#   - 初めて配備する unit（unit ファイルが無かった）: enable --now（既定のサービスを起動する）
#   - 以前から有る unit で enable（最後に「起動」した）: 新しいコードで restart
#   - 以前から有る unit で disable（最後に「停止」した）: 停止のまま
# reboot では enable の unit だけが起動する（WantedBy=multi-user.target）。
configure_systemd() {
  local service unit
  local new_units=()
  local keep_units=("${BACKEND_UNIT}" "${WORKER_UNIT}")
  local controllable_units=()
  local path name

  log "Writing systemd units."
  write_backend_unit \
    "${SYSTEMD_UNIT_DIR}/${BACKEND_UNIT}" \
    "Production Ready RAG backend" \
    "${BACKEND_DIR}/.venv/bin/gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind ${BACKEND_HOST}:${BACKEND_PORT} --workers 2 --timeout 60 --graceful-timeout 30 --keep-alive 5 --access-logfile - --error-logfile - --no-control-socket" \
    "Environment=RAG_INGESTION_QUEUE_INPROCESS_WORKER_ENABLED=false
Environment=RAG_PARSER_READINESS_PROBE_ENABLED=true
Restart=always
RestartSec=5
"
  # 取込キューの consumer はこの unit だけ（backend の in-process worker は上で無効にする）。
  # SIGTERM は main process だけに送り、子プロセスは worker が後始末する（#305 / #357）。
  write_backend_unit \
    "${SYSTEMD_UNIT_DIR}/${WORKER_UNIT}" \
    "Production Ready RAG ingestion worker" \
    "${BACKEND_DIR}/.venv/bin/python -m app.rag.ingestion_worker" \
    "Environment=RAG_INGESTION_QUEUE_SHUTDOWN_GRACE_SECONDS=${WORKER_SHUTDOWN_GRACE_SECONDS}
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=${WORKER_TIMEOUT_STOP_SEC}
Restart=on-failure
RestartSec=5
"

  for service in "${RAG_SERVICES[@]}"; do
    unit="$(rag_unit_name "${service}")"
    if [ ! -f "${SYSTEMD_UNIT_DIR}/${unit}" ]; then
      new_units+=("${unit}")
    fi
    rag_write_microservice_unit "${SYSTEMD_UNIT_DIR}/${unit}" "${service}" "${APP_REPO_DIR}" \
      "${SERVICE_USER}" "${SERVICE_GROUP}" "${SERVICE_HOME}" "${SERVICE_RUNTIME_ENV_FILE}" \
      "${PLATFORM_ENV_FILE}" "${BACKEND_DIR}/.env"
    keep_units+=("${unit}")
    controllable_units+=("${unit}")
  done

  # stack で選ばなくなったサービスの unit は止めて消す。
  for path in "${SYSTEMD_UNIT_DIR:?}/${RAG_UNIT_PREFIX:?}"*.service; do
    [ -e "${path}" ] || continue
    name="$(basename "${path}")"
    if ! contains_value "${name}" "${keep_units[@]}"; then
      log "Removing systemd unit that is no longer deployed: ${name}"
      systemctl disable --now "${name}" || true
      rm -f -- "${path}"
    fi
  done

  systemctl daemon-reload
  systemctl enable "${BACKEND_UNIT}" "${WORKER_UNIT}"
  systemctl restart "${BACKEND_UNIT}" "${WORKER_UNIT}"

  for unit in "${controllable_units[@]}"; do
    if contains_value "${unit}" "${new_units[@]}"; then
      log "Starting ${unit} (first deployment)."
      systemctl enable --now "${unit}"
    elif systemctl is-enabled --quiet "${unit}"; then
      log "Restarting ${unit} (last operation: start)."
      systemctl restart "${unit}"
    else
      log "Keeping ${unit} stopped (last operation: stop)."
      systemctl stop "${unit}" || true
    fi
  done

  configure_sudoers "${controllable_units[@]}"
}

# backend（SERVICE_USER）に、画面から操作する unit の systemctl / journalctl だけを許可する。
configure_sudoers() {
  local target="${SUDOERS_DIR}/${RAG_SUDOERS_FILE_NAME}"
  local tmp
  tmp="$(mktemp)"
  rag_write_sudoers "${tmp}" "${SERVICE_USER}" "$@"
  if ! visudo -cf "${tmp}" >/dev/null; then
    rm -f -- "${tmp:?}"
    log "Generated sudoers for the service management screen is invalid."
    return 1
  fi
  install -d -m 0750 "${SUDOERS_DIR}"
  install -m 0440 "${tmp}" "${target}"
  chown root:root "${target}" 2>/dev/null || true
  rm -f -- "${tmp:?}"
  log "Installed ${target}."
}

# backend の RAG_MAX_UPLOAD_BYTES（backend/.env。無ければ既定値）から Nginx の client_max_body_size を作る
# （backend の上限 + multipart の余白。単位は MiB で切り上げ。Refs #306）。
nginx_client_max_body_size() {
  local env_file="${BACKEND_DIR}/.env"
  local bytes
  bytes="$(sed -n 's/^RAG_MAX_UPLOAD_BYTES=//p' "${env_file}" 2>/dev/null | tail -n 1 | tr -d "[:space:]'\"")"
  bytes="${bytes:-${RAG_DEFAULT_MAX_UPLOAD_BYTES}}"
  if ! printf '%s\n' "${bytes}" | grep -Eq '^[1-9][0-9]*$'; then
    log "RAG_MAX_UPLOAD_BYTES must be a positive integer: ${bytes}"
    return 1
  fi
  printf '%sM\n' "$(( (bytes + 1048575) / 1048576 + NGINX_UPLOAD_MARGIN_MIB ))"
}

# RAG の backend は共通認証の login（RAG_AUTH_MODE=production。構成管理者 system_admin と DB ユーザー）で UI と API を保護する。
# Nginx には認証を置かず、frontend の配信と /api/ の proxy（SSE のため buffering 無効）だけを行う。
configure_nginx() {
  local client_max_body_size
  client_max_body_size="$(nginx_client_max_body_size)" || return 1
  log "Configuring Nginx on port ${APPLICATION_PORT} (client_max_body_size ${client_max_body_size})."
  cat > "${NGINX_SITES_AVAILABLE_DIR}/production-ready-rag" <<EOF
server {
    listen ${APPLICATION_PORT};
    server_name _;

    root ${FRONTEND_DIR}/dist;
    index index.html;

    access_log /var/log/nginx/production-ready-rag-access.log;
    error_log /var/log/nginx/production-ready-rag-error.log warn;

    # backend の RAG_MAX_UPLOAD_BYTES + multipart の余白（Refs #306）。
    client_max_body_size ${client_max_body_size};
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;

    location = /api {
        return 308 /api/;
    }

    # LLM を複数回呼ぶ処理だけ待ち時間を延ばす。backend の上限（LLM 1 回の timeout の設定の上限
    # 600 秒）と画面の timeout（630 秒）より長くし、backend の 504 と理由を画面に届ける。
    # - 保存済みの回答の評価（標準回答による評価。#304）: 画面が失敗を出した後で評価を保存しない。
    # - チャット・RAG 検索の回答生成（RAG_ANSWER_TIMEOUT_SECONDS。上限 600 秒。#375）と、
    #   同じ回答生成を呼ぶ MCP（rag_search / rag_chat_send_message）。
    # - 品質評価（golden set。/api/evaluation/run・/compare。#383）: backend は評価全体を 600 秒で
    #   打ち切り、残りのケースを失敗として結果を返す。
    location ~ ^/api/(search|search/stream|search/answers/[^/]+/evaluation|evaluation/run|evaluation/compare|chat/conversations/[^/]+/messages/stream|mcp)\$ {
        proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_send_timeout 660s;
        proxy_read_timeout 660s;
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
  rm -f -- "${NGINX_SITES_ENABLED_DIR:?}/default"
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx
}

dump_service_diagnostics() {
  local unit="$1"
  log "Diagnostics for ${unit}: systemctl status"
  systemctl --no-pager --full status "${unit}" || true
  log "Diagnostics for ${unit}: recent journal"
  journalctl -u "${unit}" -n 160 --no-pager || true
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
    log "The RAG system schema was not initialized; check ADB reachability and ${PLATFORM_ENV_FILE}."
  fi
  dump_service_diagnostics "${BACKEND_UNIT}"
  return "${status}"
}

main() {
  log "Starting Compute initialization (native: uv + systemd + Nginx)."
  load_rag_services
  install_system_packages
  install_nodejs
  install_uv
  prepare_service_user
  prepare_filesystem
  migrate_from_docker_compose
  install_runtime_env
  install_wallet
  install_service_venvs
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
