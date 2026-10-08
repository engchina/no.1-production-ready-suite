#!/usr/bin/env bash
# 1 台の Compute に置いた 3 製品の Nginx の site を生成する（#1316）。suite-init.sh と test が source する。
#
#   /rag/ /nl2sql/ /agent/   各製品の frontend（/<製品>/ を base に build した dist）
#   /<製品>/api/...           prefix を外して、各製品の backend（127.0.0.1 の別々の port）へ proxy
#   /<製品>/health            backend の /api/health
#   /platform/ca.crt          platform（共通基盤）が配る自作の CA 証明書（HTTPS が on のときだけ）
#   /platform/...             platform の共有の内容のために予約する（今は ca.crt 以外は 404）
#   /                         /agent/ へ 302（Agent を配備しないときは、最初に配備した製品へ）
#
# HTTPS が on のとき: https_port（既定 443）で TLS 1.2 / 1.3、http_port（既定 80）はすべて 301 で https へ
# （http_port と https_port が同じなら転送の server は置かない）。HSTS は付けない（IP の証明書のため）。
# HTTPS が off のとき: http_port で HTTP をそのまま配信する（転送しない）。port は stack の変数（#1316）。
# backend へは $request_uri から prefix だけを外した URI をそのまま渡す（%2F などのエンコードを変えない）。

# 各製品の backend の port（127.0.0.1 だけで listen する）。ローカルの開発の port と同じ（#1316 で NL2SQL を 8000 → 8010）。
# 各製品の init_script.sh の BACKEND_PORT と同じ値にする（verify_stack_contract.py が照合する）。
# 環境変数での上書きは test が隔離した port を使うためのもので、本番では変えない。
SUITE_RAG_BACKEND_PORT="${SUITE_RAG_BACKEND_PORT:-8000}"
SUITE_NL2SQL_BACKEND_PORT="${SUITE_NL2SQL_BACKEND_PORT:-8010}"
SUITE_AGENT_BACKEND_PORT="${SUITE_AGENT_BACKEND_PORT:-8020}"
# 1 台に置く製品の並び（Nginx の location の順。/ の転送先は agent を優先する）。
SUITE_PRODUCTS_ORDER=(rag nl2sql agent)

# 公開の port（http_port / https_port）に使えない port（#1316）。Terraform の locals.tf の reserved_ports と
# reserved_port_ranges と同じ値にする（verify_stack_contract.py が照合する。手で実行したときも先に失敗させる）。
#   22: SSH / 25: SMTP / 53: DNS / 111: rpcbind / 1521・1522: Oracle の listener（ADB の接続と紛らわしい）/
#   3306: MySQL / 5432: PostgreSQL / 6379: Redis / 9090: Prometheus など運用の道具 /
#   8000・8010・8020: 各製品の backend（127.0.0.1）
SUITE_RESERVED_PORTS=(22 25 53 111 1521 1522 3306 5432 6379 9090 8000 8010 8020)
# RAG の前処理 / parser（127.0.0.1:18010〜18028。rag/scripts/rag-systemd.sh）とその予備。
SUITE_RESERVED_PORT_RANGES=("18000-18099")

# 公開の port として使えるか（1〜65535 の整数で、予約していない）。使えなければ理由を stderr に書いて 1。
suite_validate_public_port() {
  local name="$1"
  local port="$2"
  local reserved range low high
  if ! [[ "${port}" =~ ^[0-9]{1,5}$ ]] || [ "${port}" -lt 1 ] || [ "${port}" -gt 65535 ]; then
    echo "${name} must be an integer between 1 and 65535: ${port}" >&2
    return 1
  fi
  for reserved in "${SUITE_RESERVED_PORTS[@]}"; do
    if [ "${port}" -eq "${reserved}" ]; then
      echo "${name} ${port} is reserved (SSH, well-known services, or a port the suite uses on the Compute)." >&2
      return 1
    fi
  done
  for range in "${SUITE_RESERVED_PORT_RANGES[@]}"; do
    low="${range%-*}"
    high="${range#*-}"
    if [ "${port}" -ge "${low}" ] && [ "${port}" -le "${high}" ]; then
      echo "${name} ${port} is reserved for the RAG preprocessing / parser services (${range})." >&2
      return 1
    fi
  done
}

suite_backend_port() {
  case "$1" in
    rag) printf '%s\n' "${SUITE_RAG_BACKEND_PORT}" ;;
    nl2sql) printf '%s\n' "${SUITE_NL2SQL_BACKEND_PORT}" ;;
    agent) printf '%s\n' "${SUITE_AGENT_BACKEND_PORT}" ;;
    *) return 1 ;;
  esac
}

# / の転送先。Agent を配備していれば /agent/、無ければ並びの最初の製品。
suite_root_redirect_target() {
  local product
  for product in "$@"; do
    if [ "${product}" = "agent" ]; then
      printf '/agent/\n'
      return 0
    fi
  done
  for product in "${SUITE_PRODUCTS_ORDER[@]}"; do
    if suite_contains "${product}" "$@"; then
      printf '/%s/\n' "${product}"
      return 0
    fi
  done
  return 1
}

suite_contains() {
  local needle="$1"
  shift
  local value
  for value in "$@"; do
    [ "${value}" = "${needle}" ] && return 0
  done
  return 1
}

# backend へ渡す proxy の共通の header。
#   $1: Host header（Agent は WebSocket の Origin と Host の一致を確かめるため、port を含む $http_host）
#   $2: Connection header（"upgrade" なら Upgrade も渡す。"" なら keep-alive の upstream 向けに空）
_suite_proxy_headers() {
  local host_header="$1"
  local connection="$2"
  cat <<EOF
        proxy_http_version 1.1;
        proxy_set_header Host ${host_header};
        proxy_set_header X-Request-ID \$pr_request_id;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Prefix ${3};
EOF
  if [ "${connection}" = "upgrade" ]; then
    cat <<'EOF'
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
EOF
  else
    cat <<'EOF'
        proxy_set_header Connection "";
EOF
  fi
  cat <<'EOF'
        proxy_buffering off;
        proxy_cache off;
EOF
}

# 製品ごとの location。今までの各製品の init_script.sh の location（SSE・MCP・login の上限・health）を
# /<製品>/ の下へ移したもの。
#   $1: 製品  $2: frontend の dist  $3: API の client_max_body_size
_suite_product_locations() {
  local product="$1"
  local dist_dir="$2"
  local body_size="$3"
  local port host_header connection
  port="$(suite_backend_port "${product}")"
  host_header="\$host"
  connection=""
  case "${product}" in
    nl2sql) connection="upgrade" ;;
    agent)
      host_header="\$http_host"
      connection="upgrade"
      ;;
  esac

  cat <<EOF

    # ---------------------------------------------------------------- ${product}（/${product}/ → 127.0.0.1:${port}）

    location = /${product} {
        return 301 /${product}/;
    }

    location = /${product}/api {
        return 308 /${product}/api/;
    }

    # ログインの API だけ、送信元 IP ごとに緩く上限を掛ける（#1173。zone は
    # platform/templates/nginx/login-rate-limit.conf）。回数の制限の正本は backend（PLATFORM_AUTH_LOGIN_*）。
    location = /${product}/api/auth/login {
        set \$pr_service_name "production-ready-${product}";
        access_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-access.log production_ready_json if=\$pr_loggable;
        error_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-error.log warn;
        limit_req zone=pr_login burst=30 nodelay;
        limit_req_status 429;
        error_page 429 = @pr_login_rate_limited;
        proxy_pass http://127.0.0.1:${port}\$pr_suite_upstream_uri;
EOF
  _suite_proxy_headers "${host_header}" "${connection}" "/${product}"
  echo "    }"

  if [ "${product}" = "rag" ]; then
    cat <<EOF

    # LLM を複数回呼ぶ処理だけ待ち時間を延ばす（backend の上限 600 秒と画面の timeout 630 秒より長く）。
    # 保存済みの回答の評価（#304）・チャットと RAG 検索の回答生成（#375）・作成中の回答の再購読（#1175）・
    # 品質評価（#383）と、同じ回答生成を呼ぶ MCP（rag_search）。
    location ~ ^/rag/api/(search|search/stream|search/answers/[^/]+/evaluation|evaluation/run|evaluation/compare|chat/conversations/[^/]+/messages/stream|chat/conversations/[^/]+/messages/[^/]+/stream|mcp)\$ {
        set \$pr_service_name "production-ready-rag";
        access_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-rag-access.log production_ready_json if=\$pr_loggable;
        error_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-rag-error.log warn;
        client_max_body_size ${body_size};
        proxy_pass http://127.0.0.1:${port}\$pr_suite_upstream_uri;
EOF
    _suite_proxy_headers "${host_header}" "${connection}" "/${product}"
    cat <<'EOF'
        proxy_send_timeout 660s;
        proxy_read_timeout 660s;
    }
EOF
  fi

  cat <<EOF

    location /${product}/api/ {
        set \$pr_service_name "production-ready-${product}";
        access_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-access.log production_ready_json if=\$pr_loggable;
        error_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-error.log warn;
        client_max_body_size ${body_size};
        proxy_pass http://127.0.0.1:${port}\$pr_suite_upstream_uri;
EOF
  _suite_proxy_headers "${host_header}" "${connection}" "/${product}"
  cat <<EOF
    }

    location = /${product}/health {
        proxy_pass http://127.0.0.1:${port}/api/health;
        proxy_set_header Host \$host;
        access_log off;
    }

    # frontend（/${product}/ を base に build した dist）。画面の URL は index.html に返す（SPA）。
    location /${product}/ {
        set \$pr_service_name "production-ready-${product}";
        access_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-access.log production_ready_json if=\$pr_loggable;
        error_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-${product}-error.log warn;
        alias ${dist_dir}/;
        index index.html;
        try_files \$uri \$uri/ /${product}/index.html;
    }
EOF
}

# server の中身（HTTP / HTTPS で共通）。
_suite_server_body() {
  local https_enabled="$1"
  local repo_dir="$2"
  local ssl_dir="$3"
  local rag_body_size="$4"
  shift 4
  local products=("$@")
  local product body_size root_target

  root_target="$(suite_root_redirect_target "${products[@]}")"
  cat <<EOF
    server_name _;

    # 転送先を相対の Location にする（443 以外の port・IP のまま、同じ origin に戻す）。
    absolute_redirect off;

    set \$pr_service_name "production-ready-suite";
    access_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-suite-access.log production_ready_json if=\$pr_loggable;
    error_log ${SUITE_NGINX_LOG_DIR:-/var/log/nginx}/production-ready-suite-error.log warn;

    client_max_body_size 10m;
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;

    location @pr_login_rate_limited {
        default_type application/json;
        add_header Retry-After 60 always;
        return 429 '{"success":false,"data":null,"error_code":"SECURITY_RATE_LIMITED","error_messages":["ログインの試行が多すぎます。しばらく待ってから、もう一度お試しください。"]}';
    }

    location = / {
        return 302 ${root_target};
    }

    # /platform/ は platform（共通基盤）が配る内容のために予約する。今は CA 証明書だけを配り、ほかは 404。
    location /platform/ {
        return 404;
    }
EOF
  if [ "${https_enabled}" = "true" ]; then
    cat <<EOF

    # 自作の Root CA の証明書（利用者の端末に取り込んで、ブラウザの警告を消す）。秘密鍵は配らない。
    location = /platform/ca.crt {
        alias ${ssl_dir}/ca.crt;
        types { }
        default_type application/x-x509-ca-cert;
        add_header Content-Disposition 'attachment; filename="production-ready-root-ca.crt"' always;
        add_header Cache-Control "no-cache" always;
        add_header X-Content-Type-Options "nosniff" always;
    }
EOF
  fi

  for product in "${SUITE_PRODUCTS_ORDER[@]}"; do
    suite_contains "${product}" "${products[@]}" || continue
    case "${product}" in
      rag) body_size="${rag_body_size}" ;;
      nl2sql) body_size="200M" ;;
      agent) body_size="100M" ;;
    esac
    _suite_product_locations "${product}" "${repo_dir}/${product}/frontend/dist" "${body_size}"
  done

  cat <<'EOF'

    # 製品・platform の外の path は返さない（Nginx の既定のページを出さない）。
    location / {
        return 404;
    }
EOF
}

# site を標準出力に書く。
#   $1: HTTPS（true / false）  $2: suite の repository  $3: 証明書の directory  $4: RAG の API の client_max_body_size
#   $5: http_port  $6: https_port  $7...: 配備する製品（rag / nl2sql / agent）
# 環境変数 SUITE_LISTEN_ADDRESS は test が 127.0.0.1 だけで listen するためのもの（本番は全 address）。
suite_nginx_site() {
  local https_enabled="$1"
  local repo_dir="$2"
  local ssl_dir="$3"
  local rag_body_size="$4"
  local http_port="$5"
  local https_port="$6"
  shift 6
  local products=("$@")
  local address="${SUITE_LISTEN_ADDRESS:-}"
  local http_listen="${address:+${address}:}${http_port}"
  local https_listen="${address:+${address}:}${https_port}"
  local https_authority='$host'
  local product

  if [ "${#products[@]}" -eq 0 ]; then
    echo "suite_nginx_site: no product is selected" >&2
    return 1
  fi
  for product in "${products[@]}"; do
    if ! suite_contains "${product}" "${SUITE_PRODUCTS_ORDER[@]}"; then
      echo "suite_nginx_site: unknown product: ${product}" >&2
      return 1
    fi
  done
  suite_validate_public_port http_port "${http_port}" || return 1
  if [ "${https_enabled}" = "true" ]; then
    suite_validate_public_port https_port "${https_port}" || return 1
  fi
  if [ "${https_port}" != "443" ]; then
    https_authority="\$host:${https_port}"
  fi

  cat <<'EOF'
# platform/deploy/suite-init.sh が生成する（手で編集しない）。#1316
# backend へ渡す URI: /<製品>/api/... の $request_uri から製品の prefix だけを外す（query とエンコードはそのまま）。
map $request_uri $pr_suite_upstream_uri {
    default "/";
    "~^/(?:rag|nl2sql|agent)(?<pr_suite_rest>/api(?:[/?].*)?)$" $pr_suite_rest;
}
EOF

  if [ "${https_enabled}" = "true" ]; then
    cat <<EOF

EOF
    if [ "${http_port}" != "${https_port}" ]; then
      cat <<EOF
# HTTP は HTTPS へ転送するだけ（HSTS は付けない。IP の証明書と自作の CA のため）。
server {
    listen ${http_listen} default_server;
    server_name _;
    access_log off;
    return 301 https://${https_authority}\$request_uri;
}

EOF
    fi
    cat <<EOF
server {
    listen ${https_listen} ssl http2 default_server;

    ssl_certificate ${ssl_dir}/server.crt;
    ssl_certificate_key ${ssl_dir}/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:pr_suite_ssl:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

EOF
  else
    cat <<EOF

server {
    listen ${http_listen} default_server;

EOF
  fi
  _suite_server_body "${https_enabled}" "${repo_dir}" "${ssl_dir}" "${rag_body_size}" "${products[@]}"
  echo "}"
}
