#!/usr/bin/env bash
# Собирает приватный образ runtime-VPS (infra#134) в проекте runtime и печатает его id.
#   source pulumi/bootstrap/env.sh
#   OS_REGION_NAME=ru-9 OS_PROJECT_ID=<runtimeProjectId> scripts/build-runtime-image.sh
# Временный сервер из базового образа ставит состав runtime-image/versions.env через cloud-init и
# выключается; его том загружается в Glance, затем пробный том из образа проверяет зону.
# Временные сервер, тома и keypair удаляются при любом исходе. Сеть, группа, флейвор, тип тома и
# зона — из ansible vars.yml (runtime_*). Keypair — одноразовый: SSH к серверу сборки не нужен, а
# keypair provisioner принадлежит пользователю runtime. Таймаут сборки — BUILD_TIMEOUT секунд (по умолчанию 1800).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
dir="$root/runtime-image"
vars="$root/ansible/inventory/group_vars/all/vars.yml"
# shellcheck source=../runtime-image/versions.env
. "$dir/versions.env"

var() { awk -v k="$1:" '$1 == k { gsub(/"/, "", $2); print $2; exit }' "$vars"; }
net=$(var runtime_network_id)
sg=$(var runtime_security_group_id)
flavor=$(var runtime_flavor_id)
vtype=$(var runtime_volume_type)
zone=$(var runtime_availability_zone)
for v in net sg flavor vtype zone; do
  [ -n "${!v}" ] || { echo "build-runtime-image: нет runtime_* для $v в $vars" >&2; exit 1; }
done
: "${OS_PROJECT_ID:?OS_PROJECT_ID — проект runtime}" "${OS_REGION_NAME:?OS_REGION_NAME — регион runtime (ru-9)}"

name="cellestial-runtime-$IMAGE_VERSION"
tag="$name-build-$(date -u +%Y%m%dT%H%M%SZ)"
timeout=${BUILD_TIMEOUT:-1800}
work=$(mktemp -d)
server='' volume='' probe='' key=''

cleanup() {
  [ -n "$server" ] && openstack server delete --wait "$server" >/dev/null 2>&1 || true
  [ -n "$key" ] && openstack keypair delete "$key" >/dev/null 2>&1 || true
  for v in "$volume" "$probe"; do
    [ -n "$v" ] && openstack volume delete "$v" >/dev/null 2>&1 || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

since() { echo $(( $(date +%s) - $1 ))s; }
wait_for() { # тип id ожидаемый_статус таймаут
  local deadline=$(( $(date +%s) + $4 )) status
  while :; do
    status=$(openstack "$1" show "$2" -f value -c status)
    [ "$status" = "$3" ] && return 0
    case "$status" in error* | ERROR*) echo "build-runtime-image: $1 $2 — $status" >&2; return 1 ;; esac
    [ "$(date +%s)" -lt "$deadline" ] || { echo "build-runtime-image: $1 $2 — $status после $4 с" >&2; return 1; }
    sleep 10
  done
}

if openstack image show "$name" >/dev/null 2>&1; then
  echo "build-runtime-image: образ $name уже есть — поднимите IMAGE_VERSION в versions.env" >&2
  exit 1
fi

# user-data: shebang, состав, файл requirements и шаги сборки
{
  echo '#!/bin/bash'
  grep -E '^[A-Z_]+=' "$dir/versions.env" | sed 's/^/export /'
  echo "cat > /root/agent-requirements.txt <<'CELLESTIAL_REQUIREMENTS'"
  cat "$dir/agent-requirements.txt"
  echo 'CELLESTIAL_REQUIREMENTS'
  cat "$dir/build-on-vps.sh"
} > "$work/user-data"

start=$(date +%s)
echo "Том сборки ${BUILD_VOLUME_GIB} ГиБ из $BASE_IMAGE_ID"
volume=$(openstack volume create "$tag" --image "$BASE_IMAGE_ID" --size "$BUILD_VOLUME_GIB" \
  --type "$vtype" --availability-zone "$zone" -f value -c id)
wait_for volume "$volume" available 600

echo "Сервер сборки ($(since "$start"))"
ssh-keygen -q -t ed25519 -N '' -f "$work/key"
key=${tag//./-}  # имя keypair — без точек
openstack keypair create "$key" --public-key "$work/key.pub" >/dev/null
server=$(openstack server create "$tag" --flavor "$flavor" --volume "$volume" \
  --availability-zone "$zone" --network "$net" --security-group "$sg" --key-name "$key" \
  --user-data "$work/user-data" -f value -c id)
wait_for server "$server" ACTIVE 600
echo "Установка состава $IMAGE_VERSION, до $timeout с ($(since "$start"))"
if ! wait_for server "$server" SHUTOFF "$timeout"; then
  openstack console log show "$server" | grep -E 'cellestial-image|Error|error' | tail -40 >&2 || true
  exit 1
fi
openstack server delete --wait "$server"
server=''

echo "Загрузка тома в образ $name ($(since "$start"))"
image=$(openstack image create "$name" --volume "$volume" --disk-format raw --container-format bare \
  -f value -c image_id)
wait_for image "$image" active 1800
openstack volume delete "$volume"
volume=''

# Образ создаёт тома в зоне сессий (infra#130: образ без зоны ломает create volume)
echo "Пробный том из образа ($(since "$start"))"
t=$(date +%s)
probe=$(openstack volume create "$name-probe" --image "$image" --size "$BUILD_VOLUME_GIB" \
  --type "$vtype" --availability-zone "$zone" -f value -c id)
wait_for volume "$probe" available 600
echo "Том из образа создан за $(since "$t")"

echo "Готово за $(since "$start"). Образ: $image"
echo "Дальше: infra:runtimeImageId в pulumi/Pulumi.prod.yaml и runtime_image_id в vars.yml = $image"
