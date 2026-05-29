#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="oci-telegram-manager"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
MANAGER_BIN="/usr/local/bin/oci-manager"

log() { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "请用 root 运行，或执行：sudo bash install.sh"
  fi
}

prompt() {
  local label="$1" default="${2:-}" secret="${3:-false}" value
  if [[ "$secret" == "true" ]]; then
    printf "%s" "$label" > /dev/tty
    IFS= read -r -s value < /dev/tty || true
    printf "\n" > /dev/tty
  else
    if [[ -n "$default" ]]; then
      printf "%s [%s]: " "$label" "$default" > /dev/tty
    else
      printf "%s: " "$label" > /dev/tty
    fi
    IFS= read -r value < /dev/tty || true
  fi
  printf "%s" "${value:-$default}"
}

confirm() {
  local label="$1" default="${2:-Y}" answer
  answer="$(prompt "$label" "$default")"
  case "${answer,,}" in
    y|yes|是|好|确认|"") return 0 ;;
    *) return 1 ;;
  esac
}

install_deps() {
  log "安装系统依赖"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates curl git bash
  else
    fail "当前脚本仅支持 Debian/Ubuntu 系统"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    log "安装 Docker"
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker

  if docker compose version >/dev/null 2>&1; then
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    return 0
  fi
  fail "Docker Compose 不可用，请检查 Docker 安装"
}

fetch_project() {
  if [[ -z "$REPO_URL" ]]; then
    warn "未设置 REPO_URL。"
    warn "如果你是从项目目录本地运行 install.sh，将复制当前目录；否则请输入 Git 仓库地址。"
    REPO_URL="$(prompt "Git 仓库地址，可留空使用当前目录")"
  fi

  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [[ -n "$REPO_URL" ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
      log "更新已有项目：$INSTALL_DIR"
      git -C "$INSTALL_DIR" fetch --all --prune
      git -C "$INSTALL_DIR" checkout "$BRANCH"
      git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    else
      rm -rf "$INSTALL_DIR"
      log "克隆项目到 $INSTALL_DIR"
      git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
  else
    local src
    src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    log "从当前目录复制项目：$src -> $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    tar --exclude='.git' --exclude='.venv' -C "$src" -cf - . | tar -C "$INSTALL_DIR" -xf -
  fi
}

write_env() {
  cd "$INSTALL_DIR"
  mkdir -p data/oci
  # The container runs as uid 10001 (oci-manager). Host bind mounts must be
  # readable/writable by that uid, otherwise startup fails with PermissionError.
  chown -R 10001:users data 2>/dev/null || chown -R 10001:100 data
  chmod 700 data data/oci

  local existing_bot="" existing_users="6327047192" existing_cf_token="" existing_cf_zone="" existing_record=""
  if [[ -f .env ]]; then
    existing_bot="$(grep -E '^BOT_TOKEN=' .env | cut -d= -f2- || true)"
    existing_users="$(grep -E '^ALLOWED_USER_IDS=' .env | cut -d= -f2- || true)"
    existing_cf_token="$(grep -E '^CLOUDFLARE_API_TOKEN=' .env | cut -d= -f2- || true)"
    existing_cf_zone="$(grep -E '^CLOUDFLARE_ZONE_ID=' .env | cut -d= -f2- || true)"
    existing_record="$(grep -E '^DEFAULT_CLOUDFLARE_RECORD=' .env | cut -d= -f2- || true)"
  fi

  local bot_token allowed_users enable_cf cf_token cf_zone default_record
  bot_token="$(prompt "请输入 Telegram Bot Token" "$existing_bot" true)"
  [[ -n "$bot_token" ]] || fail "BOT_TOKEN 不能为空"
  allowed_users="$(prompt "请输入允许访问的 Telegram 用户 ID，多个用逗号分隔" "${existing_users:-6327047192}")"
  [[ -n "$allowed_users" ]] || fail "ALLOWED_USER_IDS 不能为空"

  enable_cf="$(prompt "是否启用 Cloudflare DNS 管理？y/N" "N")"
  if [[ "${enable_cf,,}" =~ ^(y|yes)$ ]]; then
    cf_token="$(prompt "请输入 Cloudflare API Token" "$existing_cf_token" true)"
    cf_zone="$(prompt "请输入 Cloudflare Zone ID" "$existing_cf_zone")"
    default_record="$(prompt "默认 DNS 记录名，可留空" "$existing_record")"
  else
    cf_token="$existing_cf_token"
    cf_zone="$existing_cf_zone"
    default_record="$existing_record"
  fi

  umask 077
  cat > .env <<EOF_ENV
BOT_TOKEN=${bot_token}
ALLOWED_USER_IDS=${allowed_users}
DATA_DIR=/app/data
CLOUDFLARE_API_TOKEN=${cf_token}
CLOUDFLARE_ZONE_ID=${cf_zone}
DEFAULT_CLOUDFLARE_RECORD=${default_record}
EOF_ENV
  chmod 600 .env
}

write_manager() {
  cat > "$MANAGER_BIN" <<'EOF_MANAGER'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/oci-telegram-manager"
cd "$APP_DIR"
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}
case "${1:-status}" in
  start) compose up -d ;;
  stop) compose down ;;
  restart) compose restart ;;
  status) compose ps ;;
  logs) compose logs -f --tail=200 ;;
  update)
    git pull --ff-only || true
    compose build --pull
    compose up -d
    ;;
  uninstall)
    compose down
    echo "数据仍保留在 $APP_DIR/data；如需彻底删除请手动 rm -rf $APP_DIR"
    ;;
  *)
    echo "Usage: oci-manager {start|stop|restart|status|logs|update|uninstall}"
    exit 1
    ;;
esac
EOF_MANAGER
  chmod +x "$MANAGER_BIN"
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

start_app() {
  cd "$INSTALL_DIR"
  log "构建并启动容器"
  compose build
  compose up -d
}

main() {
  need_root
  printf '\nOCI Telegram Manager 一键安装脚本\n\n'
  install_deps
  fetch_project
  write_env
  write_manager
  start_app
  log "安装完成"
  compose -f "$INSTALL_DIR/docker-compose.yml" ps
  cat <<EOF_DONE

管理命令：
  oci-manager status
  oci-manager logs
  oci-manager restart
  oci-manager update
  oci-manager uninstall

下一步：
  1. 打开你的 Telegram Bot，发送 /start
  2. 上传 OCI config 文件
  3. 上传 oci_api_key.pem 文件
  4. 点击“实例列表”

EOF_DONE
}

main "$@"
