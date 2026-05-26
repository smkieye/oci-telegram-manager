#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${APP_DIR:-/opt/oci-telegram-manager}"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

cd "$APP_DIR" 2>/dev/null && compose down || true
rm -f /usr/local/bin/oci-manager
printf '已停止服务。数据目录仍保留：%s/data\n如需彻底删除：rm -rf %s\n' "$APP_DIR" "$APP_DIR"
