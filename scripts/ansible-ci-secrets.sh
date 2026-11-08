#!/usr/bin/env bash
# Shared SSH/Vault setup. Callers own cloud credentials and always-run cleanup.
set -euo pipefail
for name in DEPLOY_SSH_KEY SSH_KNOWN_HOSTS ANSIBLE_VAULT_PASSWORD RUNNER_TEMP GITHUB_ENV; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::Missing $name" >&2
    exit 1
  fi
done
[[ $# -gt 0 ]] || { echo '::error::Trusted hostnames required' >&2; exit 1; }
umask 077
d="$RUNNER_TEMP/infra"
mkdir -p "$d"
chmod 700 "$d"
printf '%s\n' "$DEPLOY_SSH_KEY" > "$d/ci-key"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$d/known_hosts"
printf '%s\n' "$ANSIBLE_VAULT_PASSWORD" > "$d/vault-pass"
chmod 600 "$d/ci-key" "$d/known_hosts" "$d/vault-pass"
ssh-keygen -y -P '' -f "$d/ci-key" > "$d/ci-key.pub" 2>/dev/null
for host in "$@"; do
  ssh-keygen -F "$host" -f "$d/known_hosts" > /dev/null
done
{
  echo "ANSIBLE_VAULT_PASSWORD_FILE=$d/vault-pass"
  echo "INFRA_SSH_ARGS=-o UserKnownHostsFile=$d/known_hosts -o StrictHostKeyChecking=yes"
  echo "INFRA_DEPLOY_KEY=$d/ci-key"
} >> "$GITHUB_ENV"
