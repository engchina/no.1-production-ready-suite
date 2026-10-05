#!/usr/bin/env bash
# 3 製品の init_script.sh が書く Nginx の site を隔離した Nginx で動かし、ログインの API だけに
# 送信元 IP ごとの緩い上限が掛かることを実 HTTP で検証する（#1173）。
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
nginx_bin="${NGINX_BIN:-nginx}"
task_dir="$(mktemp -d)"
cleanup() {
  if [ -f "${task_dir}/nginx.pid" ]; then
    kill "$(cat "${task_dir}/nginx.pid")" 2>/dev/null || true
  fi
  rm -rf -- "${task_dir}"
}
trap cleanup EXIT

free_port() {
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
}

for product in rag nl2sql agent; do
  rm -rf -- "${task_dir:?}"/*
  port="$(free_port)"
  backend_port="$(free_port)"
  # configure_nginx の heredoc（site）を取り出し、変数を隔離した値に置き換える。
  python3 - "${repo}/${product}/init_script.sh" "${task_dir}" "${port}" "${backend_port}" <<'PY'
import pathlib, re, sys
script, task_dir, port, backend_port = sys.argv[1:]
text = pathlib.Path(script).read_text()
body = text[text.index("configure_nginx() {"):]
site = body[body.index("<<EOF\n") + len("<<EOF\n"):body.index("\nEOF\n")]
values = {
    "APPLICATION_PORT": f"127.0.0.1:{port}",
    "FRONTEND_DIR": task_dir,
    "BACKEND_HOST": "127.0.0.1",
    "BACKEND_PORT": backend_port,
    "client_max_body_size": "100M",
}
def substitute(match):
    name = match.group(1)
    if name not in values:
        raise SystemExit(f"{script}: 未知の変数 {name}")
    return values[name]
site = re.sub(r"(?<!\\)\$\{(\w+)\}", substitute, site).replace("\\$", "$")
site = site.replace("/var/log/nginx/", f"{task_dir}/")
pathlib.Path(task_dir, "site.conf").write_text(site)
PY
  cat > "${task_dir}/nginx.conf" <<CONF
pid ${task_dir}/nginx.pid;
error_log ${task_dir}/error.log warn;
events {}
http {
  client_body_temp_path ${task_dir}/body;
  proxy_temp_path ${task_dir}/proxy;
  fastcgi_temp_path ${task_dir}/fastcgi;
  uwsgi_temp_path ${task_dir}/uwsgi;
  scgi_temp_path ${task_dir}/scgi;
  include ${repo}/platform/templates/nginx/logging.conf;
  include ${repo}/platform/templates/nginx/login-rate-limit.conf;
  include ${task_dir}/site.conf;
  # backend の代わり。
  server {
    listen 127.0.0.1:${backend_port};
    access_log off;
    location / {
      if (\$arg_backend_limited) { return 429 '{"error_code":"BACKEND_429"}'; }
      return 200 '{"success":true}';
    }
  }
}
CONF
  "${nginx_bin}" -t -q -c "${task_dir}/nginx.conf" -p "${task_dir}"
  "${nginx_bin}" -c "${task_dir}/nginx.conf" -p "${task_dir}"
  python3 - "${product}" "${port}" <<'PY'
import json, sys, urllib.error, urllib.request
product, port = sys.argv[1:]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def call(path, method):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}" if method == "POST" else None, method=method)
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
# ほかの API（ログイン以外）には掛からない。
assert all(call("/api/auth/me", "GET")[0] == 200 for _ in range(40)), product
# backend の 429（Retry-After の秒数を持つ）は、Nginx の応答に置き換えずにそのまま返す。
status, _, body = call("/api/auth/login?backend_limited=1", "POST")
assert (status, json.loads(body)) == (429, {"error_code": "BACKEND_429"}), (product, status, body)
results = [call("/api/auth/login", "POST") for _ in range(40)]
statuses = [status for status, _, _ in results]
# 1 秒あたり 1 回・burst 30 なので、人の操作や NAT の後ろの複数の利用者では掛からない。
assert statuses[:30] == [200] * 30, (product, statuses)
assert 429 in statuses, (product, statuses)
status, headers, body = next(result for result in results if result[0] == 429)
payload = json.loads(body.decode("utf-8"))
assert payload["error_code"] == "SECURITY_RATE_LIMITED", payload
assert payload["error_messages"] == ["ログインの試行が多すぎます。しばらく待ってから、もう一度お試しください。"], payload
assert headers.get("Retry-After") == "60", headers
assert headers.get("Content-Type", "").startswith("application/json"), headers
print(f"{product}: Nginx のログインの上限: pass")
PY
  "${nginx_bin}" -s quit -c "${task_dir}/nginx.conf" -p "${task_dir}"
  for _ in $(seq 50); do
    [ -f "${task_dir}/nginx.pid" ] || break
    sleep 0.1
  done
done
