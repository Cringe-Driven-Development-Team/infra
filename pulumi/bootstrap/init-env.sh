#!/usr/bin/env bash
# Создаёт ~/.config/selectel.env (права 600) для bootstrap-стека: bash init-env.sh <сервисный-пользователь>
# Пароль Selectel и passphrase стеков вводятся скрыто. Запускать через bash и в macOS (где оболочка
# по умолчанию zsh, а у zsh другой синтаксис read). Путь переопределяется SELECTEL_ENV.
set -euo pipefail

user=${1:-}
[ -n "$user" ] || { echo "использование: bash init-env.sh <сервисный-пользователь-selectel>" >&2; exit 2; }
file=${SELECTEL_ENV:-$HOME/.config/selectel.env}
[ ! -e "$file" ] || { echo "init-env: $file уже есть — не перезаписываю" >&2; exit 1; }

read -rsp 'Пароль сервисного пользователя Selectel: ' password; echo >&2
read -rsp 'Passphrase стеков Pulumi (менеджер паролей команды): ' passphrase; echo >&2
[ -n "$password" ] && [ -n "$passphrase" ] || { echo "init-env: пароль и passphrase не могут быть пустыми" >&2; exit 1; }

mkdir -p "$(dirname "$file")"
(umask 077 && printf 'SELECTEL_USERNAME=%s\nSELECTEL_PASSWORD=%s\nSELECTEL_DOMAIN_NAME=631994\nPULUMI_CONFIG_PASSPHRASE=%s\n' \
  "$user" "$password" "$passphrase" > "$file")
chmod 600 "$file"
echo "init-env: записан $file" >&2
