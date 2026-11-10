# Выполняется cloud-init на временном сервере сборки (scripts/build-runtime-image.sh добавляет
# сверху shebang, versions.env и статические файлы ролей runtime в /root/static). Успех — poweroff; ошибка — сервер остаётся
# включённым, локальный скрипт ждёт SHUTOFF и по таймауту показывает console log.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
log() { echo "cellestial-image: $*" | tee /dev/console; }
trap 'log "FAILED at line $LINENO"' ERR

log "build $IMAGE_VERSION started"

# Без фоновых apt: образ не меняется сам, сессия не ждёт блокировку dpkg
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service 2>/dev/null || true
systemctl mask apt-daily.service apt-daily-upgrade.service unattended-upgrades.service

# Ubuntu — snapshot на UBUNTU_SNAPSHOT вместо зеркала образа
mv /etc/apt/sources.list.d/ubuntu.sources /root/ubuntu.sources.orig 2>/dev/null || true
: > /etc/apt/sources.list
cat > /etc/apt/sources.list.d/ubuntu-snapshot.sources <<EOF
Types: deb
URIs: https://snapshot.ubuntu.com/ubuntu/$UBUNTU_SNAPSHOT/
Suites: resolute resolute-updates resolute-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

# Репозиторий Docker: ключ только с ожидаемым отпечатком
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
fpr=$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '/^fpr/{print $10; exit}')
[ "$fpr" = "$DOCKER_GPG_FINGERPRINT" ] || { log "docker key fingerprint $fpr"; false; }
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: resolute
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
# shellcheck disable=SC2086 # списки пакетов — слова
apt-get install -y --no-install-recommends $APT_PACKAGES $DOCKER_PACKAGES
# shellcheck disable=SC2086,SC2046
apt-mark hold $(printf '%s\n' $APT_PACKAGES $DOCKER_PACKAGES | cut -d= -f1)
log "packages installed"

# Сеть kernel, слои kernel Python; Docker не стартует при загрузке: его запускает плейбук после firewall
docker network create --driver bridge --ipv4 --ipv6=false --subnet 172.30.0.0/24 \
  --opt com.docker.network.bridge.name=cellestial0 --opt com.docker.network.bridge.enable_icc=false \
  cellestial-kernel
docker pull "$PYTHON_KERNEL_IMAGE"
log "kernel image pulled"

# Агент из образа backend: wheel (по sha256), requirements, unit — те же файлы, что получит сессия
docker pull "$BACKEND_IMAGE"
cid=$(docker create "$BACKEND_IMAGE")
docker cp "$cid:/app/agent/." /root/agent/
docker rm "$cid" >/dev/null
docker rmi "$BACKEND_IMAGE" >/dev/null
wheel=$(ls /root/agent/*.whl)
echo "$(cat "$wheel.sha256")  $wheel" | sha256sum -c -

useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
  --groups docker cellestial-agent
install -d -m 0755 -o root -g root /opt/cellestial-agent /etc/cellestial-agent
install -m 0644 -o root -g root /root/agent/requirements.txt /opt/cellestial-agent/requirements.txt
install -m 0644 -o root -g root "$wheel" "/opt/cellestial-agent/$(basename "$wheel")"
python3 -m venv /opt/cellestial-agent/venv
/opt/cellestial-agent/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
  -r /opt/cellestial-agent/requirements.txt
/opt/cellestial-agent/venv/bin/pip install --no-cache-dir --disable-pip-version-check --no-deps \
  "/opt/cellestial-agent/$(basename "$wheel")"
install -m 0644 -o root -g root /root/agent/cellestial-agent.service /etc/systemd/system/cellestial-agent.service
log "agent installed"

# Настройки, не зависящие от сессии — файлы ролей runtime как есть (плейбук сессии видит их без изменений)
install -d -m 0755 /etc/cellestial /etc/systemd/system/nginx.service.d /etc/systemd/system/cellestial-agent.service.d
install -d -m 0700 /etc/nginx/cellestial
install -m 0644 /root/static/sysctl.conf /etc/sysctl.d/90-cellestial-runtime.conf
install -m 0644 /root/static/cellestial-firewall.service /etc/systemd/system/cellestial-firewall.service
install -m 0644 /root/static/nginx-network.conf /etc/systemd/system/nginx.service.d/10-cellestial-network.conf
install -m 0644 /root/static/firewall-dropin.conf /etc/systemd/system/cellestial-agent.service.d/10-cellestial-firewall.conf
rm -f /etc/nginx/sites-enabled/default
systemctl daemon-reload
systemctl disable docker.service docker.socket
log "settings applied"

# Маркер состава: плейбук сессии сверяет его с runtime_host_image_version
echo "$IMAGE_VERSION" > /etc/cellestial/runtime-image
chmod 0644 /etc/cellestial/runtime-image

# Ключ provisioner — для root, как в образах Selectel
cat > /etc/cloud/cloud.cfg.d/90-cellestial.cfg <<'EOF'
disable_root: false
EOF

df -h / | tee /dev/console

# Чистый первый запуск для каждой сессии: cloud-init, ключи хоста, machine-id, ключи сборки
apt-get clean
rm -rf /var/lib/apt/lists/* /root/agent /root/static
rm -f /etc/ssh/ssh_host_* /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys
journalctl --rotate && journalctl --vacuum-time=1s
cloud-init clean --logs --machine-id
log "build $IMAGE_VERSION OK"
sync
poweroff
