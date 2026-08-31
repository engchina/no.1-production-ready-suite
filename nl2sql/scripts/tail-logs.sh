#!/usr/bin/env bash
# OCI Compute へデプロイした後のアプリログをリアルタイムに表示する読み取り専用ビューア。
# systemd unit(journald)と /var/log 配下のログを 1 コマンドで統合表示する。
# サービスの再起動など状態を変更する操作は一切行わない。
set -Eeuo pipefail

# unit 名は scripts/update-after-pull.sh の BACKEND_SERVICE / WORKER_SERVICES と同一。
# 追加・改名する場合は両方を必ず揃えること。
BACKEND_SERVICE="production-ready-nl2sql-backend.service"
WORKER_SERVICES=(
  "production-ready-nl2sql-schema-refresh-worker.service"
  "production-ready-nl2sql-quality-evaluation-worker.service"
  "production-ready-nl2sql-ontology-worker.service"
)
ALL_SERVICES=("${BACKEND_SERVICE}" "${WORKER_SERVICES[@]}")

NGINX_ACCESS_LOG="${NGINX_ACCESS_LOG:-/var/log/nginx/production-ready-nl2sql-access.log}"
NGINX_ERROR_LOG="${NGINX_ERROR_LOG:-/var/log/nginx/production-ready-nl2sql-error.log}"
INIT_LOG_PATH="${INIT_LOG_PATH:-/var/log/nl2sql-init.log}"
UPDATE_LOG_PATH="${UPDATE_LOG_PATH:-/var/log/nl2sql-update.log}"

BACKEND_HEALTH_URL="${BACKEND_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:-http://127.0.0.1/api/health}"
HEALTHCHECK_TIMEOUT_SECONDS="${HEALTHCHECK_TIMEOUT_SECONDS:-5}"

ACTION="tail"
LINES="${TAIL_LOGS_LINES:-200}"
SINCE=""
GREP_PATTERN=""
MIN_LEVEL=""
FOLLOW=true
RAW=false
COLOR_ENABLED=false
SHOW_UNIT=false
UNIT_SELECTION_MADE=false
FILE_SELECTION_MADE=false
NO_COLOR_REQUESTED=false
PYTHON_BIN=""
SELECTED_UNITS=()
SELECTED_FILES=()   # "label=path" 形式
JOURNAL_ARGS=()
JOURNAL_PRIVILEGED=()
PIDS=()
_cleaning=0

log() {
  printf '[nl2sql-logs] %s\n' "$*"
}

warn() {
  printf '[nl2sql-logs] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[nl2sql-logs] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./scripts/tail-logs.sh [options]

OCI Compute 上のアプリログをリアルタイム表示します(読み取り専用)。
引数なしで backend と worker 3 本の systemd unit を統合追尾します。

対象の選択:
  --backend         backend unit のみを追尾します。
  --workers         worker 3 unit のみを追尾します。
  --unit <name>     unit を個別指定します(複数回指定可)。
                    backend / schema-refresh / quality-evaluation / ontology の
                    短縮名と完全な unit 名の双方を受理します。
  --nginx           Nginx の access / error ログを対象にします。
  --init            /var/log/nl2sql-init.log を対象にします。
  --update          /var/log/nl2sql-update.log を対象にします。
  --all             アプリ 4 unit + Nginx + init + update をすべて対象にします。

  --nginx / --init / --update を単独指定した場合は、そのファイルだけを表示します。
  unit も同時に見るときは --all または --backend などと併用してください。

表示の調整:
  -n, --lines <N>   追尾開始前に遡って表示する行数(既定: 200)。
  --since <expr>    journalctl --since へ委譲します(例: "-15 min", today)。
  --grep <pattern>  拡張正規表現で行を絞り込みます。
  --level <LEVEL>   DEBUG / INFO / WARNING / ERROR / CRITICAL の下限で絞り込みます。
                    JSON として解析できた行にのみ適用し、traceback など解析できない
                    行は取りこぼしを防ぐため常に表示します。
  --no-follow       追尾せず、一括出力して終了します(報告用スナップショット)。
  --raw             整形せず journalctl の生出力(-o short-iso)を表示します。
  --no-color        色付けを無効にします(環境変数 NO_COLOR でも同じ)。

その他:
  --status          追尾せず、unit の稼働状態とヘルスチェック結果を表示して終了します。
  -h, --help        このヘルプを表示します。

journald は権限不足のとき自動的に sudo へフォールバックします。
JSON ログの整形には python3 を使用します(未導入の場合は生出力へ縮退)。

Environment overrides:
  NGINX_ACCESS_LOG / NGINX_ERROR_LOG   Nginx ログのパス
  INIT_LOG_PATH / UPDATE_LOG_PATH      init / update ログのパス
  BACKEND_HEALTH_URL / PUBLIC_HEALTH_URL --status のヘルスチェック URL
EOF
}

usage_error() {
  printf '[nl2sql-logs] ERROR: %s\n' "$*" >&2
  usage >&2
  exit 2
}

# 短縮名と完全な unit 名の双方を受理して完全な unit 名へ解決する。
resolve_unit() {
  case "$1" in
    backend) printf '%s\n' "${BACKEND_SERVICE}" ;;
    schema-refresh | schema) printf '%s\n' "production-ready-nl2sql-schema-refresh-worker.service" ;;
    quality-evaluation | quality) printf '%s\n' "production-ready-nl2sql-quality-evaluation-worker.service" ;;
    ontology) printf '%s\n' "production-ready-nl2sql-ontology-worker.service" ;;
    production-ready-nl2sql-*.service) printf '%s\n' "$1" ;;
    production-ready-nl2sql-*) printf '%s\n' "$1.service" ;;
    *) return 1 ;;
  esac
}

add_unit() {
  local unit="$1" existing
  for existing in ${SELECTED_UNITS[@]+"${SELECTED_UNITS[@]}"}; do
    [ "${existing}" = "${unit}" ] && return 0
  done
  SELECTED_UNITS+=("${unit}")
}

add_file() {
  local label="$1" path="$2" existing
  for existing in ${SELECTED_FILES[@]+"${SELECTED_FILES[@]}"}; do
    [ "${existing#*=}" = "${path}" ] && return 0
  done
  SELECTED_FILES+=("${label}=${path}")
}

select_backend() {
  UNIT_SELECTION_MADE=true
  add_unit "${BACKEND_SERVICE}"
}

select_workers() {
  local unit
  UNIT_SELECTION_MADE=true
  for unit in "${WORKER_SERVICES[@]}"; do
    add_unit "${unit}"
  done
}

select_all_units() {
  local unit
  UNIT_SELECTION_MADE=true
  for unit in "${ALL_SERVICES[@]}"; do
    add_unit "${unit}"
  done
}

select_nginx_files() {
  FILE_SELECTION_MADE=true
  add_file "nginx-access" "${NGINX_ACCESS_LOG}"
  add_file "nginx-error" "${NGINX_ERROR_LOG}"
}

require_value() {
  [ "$#" -ge 2 ] || usage_error "$1 には値が必要です。"
}

parse_args() {
  local unit
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --backend)
        select_backend
        ;;
      --workers)
        select_workers
        ;;
      --unit)
        require_value "$@"
        unit="$(resolve_unit "$2")" || usage_error "不明な unit です: $2"
        UNIT_SELECTION_MADE=true
        add_unit "${unit}"
        shift
        ;;
      --nginx)
        select_nginx_files
        ;;
      --init)
        FILE_SELECTION_MADE=true
        add_file "init" "${INIT_LOG_PATH}"
        ;;
      --update)
        FILE_SELECTION_MADE=true
        add_file "update" "${UPDATE_LOG_PATH}"
        ;;
      --all)
        select_all_units
        select_nginx_files
        add_file "init" "${INIT_LOG_PATH}"
        add_file "update" "${UPDATE_LOG_PATH}"
        ;;
      -n | --lines)
        require_value "$@"
        case "$2" in
          '' | *[!0-9]*) usage_error "--lines には 0 以上の整数を指定してください: $2" ;;
        esac
        LINES="$2"
        shift
        ;;
      --since)
        require_value "$@"
        SINCE="$2"
        shift
        ;;
      --grep)
        require_value "$@"
        GREP_PATTERN="$2"
        shift
        ;;
      --level)
        require_value "$@"
        case "$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')" in
          DEBUG | INFO | WARNING | ERROR | CRITICAL) MIN_LEVEL="$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')" ;;
          *) usage_error "--level には DEBUG / INFO / WARNING / ERROR / CRITICAL を指定してください: $2" ;;
        esac
        shift
        ;;
      --no-follow)
        FOLLOW=false
        ;;
      --raw)
        RAW=true
        ;;
      --no-color)
        NO_COLOR_REQUESTED=true
        ;;
      --status)
        ACTION="status"
        ;;
      -h | --help)
        ACTION="help"
        return 0
        ;;
      *)
        usage_error "不明な引数です: $1"
        ;;
    esac
    shift
  done
}

# 対象未指定なら 4 unit を既定にする。ファイルだけを指定した場合は unit を追尾しない。
resolve_sources() {
  if [ "${UNIT_SELECTION_MADE}" = false ] && [ "${FILE_SELECTION_MADE}" = false ]; then
    select_all_units
  fi
  if [ "${#SELECTED_UNITS[@]}" -gt 1 ]; then
    SHOW_UNIT=true
  fi
}

resolve_color() {
  COLOR_ENABLED=false
  [ "${NO_COLOR_REQUESTED}" = true ] && return 0
  [ -n "${NO_COLOR:-}" ] && return 0
  [ -t 1 ] || return 0
  COLOR_ENABLED=true
}

resolve_python() {
  PYTHON_BIN=""
  [ "${RAW}" = true ] && return 0
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
    return 0
  fi
  warn "python3 が見つからないため、JSON ログを整形せずそのまま表示します。"
}

# journald を読めるか確認し、必要なら sudo へフォールバックする。
resolve_journal_access() {
  JOURNAL_PRIVILEGED=()
  command -v journalctl >/dev/null 2>&1 || die "journalctl が見つかりません。systemd のあるホストで実行してください。"
  if journalctl -n 0 --no-pager >/dev/null 2>&1; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo journalctl -n 0 --no-pager >/dev/null 2>&1; then
    JOURNAL_PRIVILEGED=(sudo)
    return 0
  fi
  die "journald を読み取れません。sudo ./scripts/tail-logs.sh で実行してください。"
}

build_journal_args() {
  local unit
  JOURNAL_ARGS=(--no-pager -n "${LINES}")
  for unit in ${SELECTED_UNITS[@]+"${SELECTED_UNITS[@]}"}; do
    JOURNAL_ARGS+=(-u "${unit}")
  done
  [ -n "${SINCE}" ] && JOURNAL_ARGS+=(--since "${SINCE}")
  [ -n "${GREP_PATTERN}" ] && JOURNAL_ARGS+=(-g "${GREP_PATTERN}")
  if [ "${RAW}" = true ] || [ -z "${PYTHON_BIN}" ]; then
    JOURNAL_ARGS+=(-o short-iso)
  else
    JOURNAL_ARGS+=(-o json)
  fi
  [ "${FOLLOW}" = true ] && JOURNAL_ARGS+=(-f)
  return 0
}

# journald の JSON 1 行を人間が読める 1 行へ整形するストリームフィルタ。
read -r -d '' FORMATTER_PY <<'PYEOF' || true
import json
import os
import sys

LEVELS = {
    "DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30,
    "ERROR": 40, "CRITICAL": 50, "FATAL": 50,
}
COLORS = {"WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[31m"}
RESET = "\033[0m"
SKIP_KEYS = {"timestamp", "asctime", "level", "levelname", "name", "message", "taskName"}
UNIT_PREFIX = "production-ready-nl2sql-"

min_level = LEVELS.get(os.environ.get("TAIL_LOGS_MIN_LEVEL", "").upper(), 0)
show_unit = os.environ.get("TAIL_LOGS_SHOW_UNIT") == "true"
use_color = os.environ.get("TAIL_LOGS_COLOR") == "true"


def short_unit(unit):
    name = unit or ""
    if name.endswith(".service"):
        name = name[: -len(".service")]
    if name.startswith(UNIT_PREFIX):
        name = name[len(UNIT_PREFIX):]
    return name


def short_time(value, realtime_usec):
    if value:
        text = str(value)
        if " " in text:
            text = text.split(" ", 1)[1]
        elif "T" in text:
            text = text.split("T", 1)[1]
        return text[:12].replace(",", ".").ljust(12)
    if realtime_usec:
        try:
            import datetime

            stamp = datetime.datetime.fromtimestamp(int(realtime_usec) / 1_000_000)
            return stamp.strftime("%H:%M:%S.%f")[:12].ljust(12)
        except (ValueError, OverflowError, OSError):
            pass
    return "--:--:--".ljust(12)


def loads(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def emit(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def render(line):
    unit = ""
    realtime_usec = ""
    message = line
    outer = loads(line)
    if isinstance(outer, dict) and "MESSAGE" in outer:
        unit = outer.get("_SYSTEMD_UNIT") or outer.get("SYSLOG_IDENTIFIER") or ""
        raw = outer.get("MESSAGE")
        if isinstance(raw, list):
            # journald はバイナリを含む MESSAGE を int 配列で返す。
            raw = bytes(bytearray(v & 0xFF for v in raw)).decode("utf-8", "replace")
        message = raw if isinstance(raw, str) else str(raw)
        realtime_usec = outer.get("__REALTIME_TIMESTAMP", "")

    record = loads(message)
    prefix = short_unit(unit) + "  " if (show_unit and unit) else ""
    if not isinstance(record, dict) or "message" not in record:
        # 解析できない行(traceback / uvicorn バナー等)は落とさずそのまま出す。
        emit(prefix + message)
        return

    level = str(record.get("level") or record.get("levelname") or "INFO").upper()
    if LEVELS.get(level, 20) < min_level:
        return

    stamp = short_time(record.get("timestamp") or record.get("asctime"), realtime_usec)
    level_text = level.ljust(8)
    if use_color and level in COLORS:
        level_text = COLORS[level] + level_text + RESET
    parts = [stamp, level_text]
    if show_unit and unit:
        parts.append(short_unit(unit).ljust(20))
    parts.append(str(record.get("name") or "-"))
    parts.append(str(record.get("message")))
    extras = []
    for key, value in record.items():
        if key in SKIP_KEYS:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        extras.append("%s=%s" % (key, value))
    text = "  ".join(parts)
    if extras:
        text += "  " + " ".join(extras)
    emit(text)


def main():
    for line in sys.stdin:
        render(line.rstrip("\n"))


try:
    main()
except KeyboardInterrupt:
    pass
except BrokenPipeError:
    pass
PYEOF

format_stream() {
  if [ -z "${PYTHON_BIN}" ]; then
    cat
    return
  fi
  TAIL_LOGS_MIN_LEVEL="${MIN_LEVEL}" \
    TAIL_LOGS_SHOW_UNIT="${SHOW_UNIT}" \
    TAIL_LOGS_COLOR="${COLOR_ENABLED}" \
    "${PYTHON_BIN}" -c "${FORMATTER_PY}"
}

start_journal_stream() {
  [ "${#SELECTED_UNITS[@]}" -gt 0 ] || return 0
  build_journal_args
  (
    "${JOURNAL_PRIVILEGED[@]}" journalctl "${JOURNAL_ARGS[@]}" | format_stream
  ) &
  PIDS+=("$!")
}

start_file_stream() {
  local label="$1" path="$2"
  local -a reader=()
  if [ ! -e "${path}" ]; then
    warn "ログファイルが存在しないため対象から外します: ${path}"
    return 0
  fi
  if [ ! -r "${path}" ]; then
    command -v sudo >/dev/null 2>&1 || {
      warn "ログファイルを読み取れないため対象から外します: ${path}"
      return 0
    }
    reader=(sudo)
  fi
  if [ "${FOLLOW}" = true ]; then
    reader+=(tail -F -n "${LINES}" "${path}")
  else
    reader+=(tail -n "${LINES}" "${path}")
  fi
  (
    if [ -n "${GREP_PATTERN}" ]; then
      "${reader[@]}" 2>/dev/null | grep --line-buffered -E -- "${GREP_PATTERN}" |
        awk -v prefix="[${label}] " '{ print prefix $0; fflush() }'
    else
      "${reader[@]}" 2>/dev/null | awk -v prefix="[${label}] " '{ print prefix $0; fflush() }'
    fi
  ) &
  PIDS+=("$!")
}

cleanup() {
  # 再入防止: 最初の Ctrl+C で全トラップを解除し、二度目以降の INT は無視する。
  if [ "${_cleaning}" -eq 1 ]; then
    return
  fi
  _cleaning=1
  trap - INT TERM EXIT
  # ジョブ制御の "Terminated" 通知を出さずに停止する。
  set +m

  local pid waited alive
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
  done
  waited=0
  while [ "${waited}" -lt 15 ]; do
    alive=0
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive=1
      fi
    done
    if [ "${alive}" -eq 0 ]; then
      break
    fi
    sleep 0.2
    waited=$((waited + 1))
  done
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 -- "-${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null || true
    fi
  done
}

print_health() {
  local url="$1" code
  code="$(curl -s -o /dev/null -m "${HEALTHCHECK_TIMEOUT_SECONDS}" -w '%{http_code}' "${url}" 2>/dev/null || true)"
  if [ -z "${code}" ] || [ "${code}" = "000" ]; then
    code="unreachable"
  fi
  printf '  %-56s %s\n' "${url}" "${code}"
}

run_status() {
  local service state
  log "systemd unit:"
  for service in "${ALL_SERVICES[@]}"; do
    state="$(systemctl is-active "${service}" 2>/dev/null || true)"
    [ -n "${state}" ] || state="unknown"
    printf '  %-56s %s\n' "${service}" "${state}"
  done
  log "ヘルスチェック:"
  print_health "${BACKEND_HEALTH_URL}"
  print_health "${PUBLIC_HEALTH_URL}"
  log "ログを追尾するには ./scripts/tail-logs.sh を引数なしで実行してください。"
}

run_tail() {
  local entry
  resolve_journal_access
  trap cleanup INT TERM
  trap cleanup EXIT
  # 各バックグラウンドジョブを独立したプロセスグループにし、Ctrl+C でまとめて停止できるようにする。
  set -m

  if [ "${#SELECTED_UNITS[@]}" -gt 0 ]; then
    log "追尾する unit: ${SELECTED_UNITS[*]}"
  fi
  for entry in ${SELECTED_FILES[@]+"${SELECTED_FILES[@]}"}; do
    start_file_stream "${entry%%=*}" "${entry#*=}"
  done
  start_journal_stream

  if [ "${#PIDS[@]}" -eq 0 ]; then
    die "追尾できるログがありません。"
  fi
  if [ "${FOLLOW}" = true ]; then
    log "Ctrl+C で終了します。"
  fi
  wait ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
}

main() {
  parse_args "$@"
  if [ "${ACTION}" = "help" ]; then
    usage
    return 0
  fi
  if [ "${ACTION}" = "status" ]; then
    run_status
    return 0
  fi
  resolve_sources
  resolve_color
  resolve_python
  run_tail
}

if [ "${TAIL_LOGS_TEST_MODE:-false}" != "true" ]; then
  main "$@"
fi
