#!/usr/bin/env bash
set -euo pipefail

workspace=/srv/cellestial/workspace
backing=/var/lib/cellestial-workspace/workspace.ext4

fail() {
    echo "Runtime workspace: $*" >&2
    exit 1
}

verify_mount() {
    local source
    source=$(findmnt --noheadings --output SOURCE --types ext4 --mountpoint "$workspace") ||
        fail "expected an ext4 mount at $workspace"
    losetup --associated "$backing" --noheadings --output NAME | grep -Fx -- "$source" >/dev/null ||
        fail "$workspace is not mounted from $backing"
}

if [[ ${1:-} == --verify-mount ]]; then
    verify_mount
    exit
fi

[[ ${1:-} =~ ^[1-9][0-9]*$ && ${#1} -le 9 ]] || fail "invalid size in GiB"
gib=$((1024 * 1024 * 1024))
size=$(( $1 * gib ))
reserve=$((4 * gib))
umask 077

# Сериализуем подготовку, чтобы параллельный запуск не заменил уже созданный образ.
exec 9>"${backing}.lock"
flock 9

required=$reserve
if [[ -e "$backing" || -L "$backing" ]]; then
    [[ -f "$backing" && ! -L "$backing" ]] || fail "unexpected backing file type"
    read -r actual blocks block_size < <(stat --format='%s %b %B' "$backing")
    [[ $actual -eq $size && $((blocks * block_size)) -ge $size ]] ||
        fail "backing file size or allocation does not match $size bytes"
else
    required=$((size + reserve))
fi

if findmnt --mountpoint "$workspace" >/dev/null; then
    verify_mount
fi

# Не учитываем блоки, зарезервированные корневой ФС для root.
read -r available_blocks fragment_size < <(stat -f --format='%a %S' "$(dirname "$backing")")
available=$((available_blocks * fragment_size))
(( available >= required )) ||
    fail "insufficient free disk space: need $required bytes, available $available bytes"

if [[ ! -e "$backing" ]]; then
    # Публикуем файл только после успешного форматирования.
    temporary=$(mktemp "${backing}.XXXXXX")
    trap 'rm -f -- "$temporary"' EXIT
    fallocate --length "$size" "$temporary"
    mkfs.ext4 -q -F -m 0 -E nodiscard "$temporary"
    mv -T -- "$temporary" "$backing"
    echo "Created workspace filesystem"
fi
