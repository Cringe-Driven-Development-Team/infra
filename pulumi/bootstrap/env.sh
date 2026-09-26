# Учётные данные Selectel для bootstrap-стека. Использование: source pulumi/bootstrap/env.sh
# Файл ~/.config/selectel.env (права 600, путь переопределяется SELECTEL_ENV) содержит строки
# KEY=value: SELECTEL_USERNAME, SELECTEL_PASSWORD, SELECTEL_DOMAIN_NAME, PULUMI_CONFIG_PASSPHRASE.
# Файл разбирается построчно, а не через source: значения со спецсимволами передаются как есть.
# POSIX sh — работает в bash, zsh и dash.
_sel_file=${SELECTEL_ENV:-$HOME/.config/selectel.env}
if [ ! -r "$_sel_file" ]; then
  echo "env.sh: нет файла $_sel_file (см. pulumi/bootstrap/README.md)" >&2
  unset _sel_file
  return 1
fi
while IFS= read -r _sel_line || [ -n "$_sel_line" ]; do
  case "$_sel_line" in
    SELECTEL_USERNAME=* | SELECTEL_PASSWORD=* | SELECTEL_DOMAIN_NAME=* | PULUMI_CONFIG_PASSPHRASE=*)
      export "${_sel_line%%=*}=${_sel_line#*=}" ;;
  esac
done < "$_sel_file"
export OS_USERNAME="${SELECTEL_USERNAME:-}"
export OS_PASSWORD="${SELECTEL_PASSWORD:-}"
export OS_DOMAIN_NAME="${SELECTEL_DOMAIN_NAME:-}"
export OS_AUTH_URL=https://cloud.api.selcloud.ru/identity/v3/
export OS_REGION_NAME=ru-7
# Личный ~/.aws не участвует: регион, endpoint и ключи стейта задаются явно, а чужой профиль
# (ca_bundle с ~, старые ключи) ломает AWS-провайдер Pulumi и pulumi login s3://.
export AWS_CONFIG_FILE=/dev/null
export AWS_SHARED_CREDENTIALS_FILE=/dev/null
if [ -z "${PULUMI_CONFIG_PASSPHRASE:-}" ]; then
  echo "env.sh: в $_sel_file нет PULUMI_CONFIG_PASSPHRASE — pulumi спросит passphrase" >&2
fi
unset _sel_file _sel_line
