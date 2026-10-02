# OpenStack для Ansible без clouds.yaml. Использование — из каталога ansible/:
#   . ./env.sh
# Учётка — из ~/.config/selectel.env (через pulumi/bootstrap/env.sh), проект и пул VPS — из
# прод-стека Pulumi (бэкенд — backend.url в pulumi/Pulumi.yaml, см. RUNBOOK §2). openstacksdk собирает из OS_*
# облако с именем OS_CLOUD_NAME — selectel, как ждут inventory/openstack.yml и verify.yml.
# Если рядом лежит clouds.yaml с облаком selectel, openstacksdk откажется: оставьте что-то одно.
# Оболочка после этого смотрит в прод-проект (OS_PROJECT_ID, OS_REGION_NAME) — для bootstrap-стека
# и state-key.ts заново source pulumi/bootstrap/env.sh.
# Пароль vault (inventory/group_vars/all/vault.yml) — из файла вне репо: ANSIBLE_VAULT_PASSWORD_FILE,
# по умолчанию ~/.config/cdd-vault-pass (права 600). Уже заданная переменная (CI) не перетирается.
# POSIX sh — работает в bash, zsh и dash.
if [ ! -r ../pulumi/bootstrap/env.sh ] || [ ! -r inventory/openstack.yml ]; then
  echo "ansible/env.sh: запускать из каталога ansible/" >&2
  return 1
fi
export ANSIBLE_VAULT_PASSWORD_FILE="${ANSIBLE_VAULT_PASSWORD_FILE:-$HOME/.config/cdd-vault-pass}"
if [ ! -r "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
  echo "ansible/env.sh: нет файла пароля vault $ANSIBLE_VAULT_PASSWORD_FILE — playbook'и не расшифруют vault.yml (README, «Секреты»)" >&2
fi
. ../pulumi/bootstrap/env.sh || return 1
_ans_stack=${INFRA_STACK:-prod}
if ! _ans_project=$(cd ../pulumi && pulumi stack output projectId --stack "$_ans_stack") ||
  ! _ans_pool=$(cd ../pulumi && pulumi config get pool --stack "$_ans_stack"); then
  echo "ansible/env.sh: не прочитать projectId/pool стека $_ans_stack (личный ключ стейта в selectel.env? задана PULUMI_BACKEND_URL?)" >&2
  unset _ans_stack _ans_project _ans_pool
  return 1
fi
unset OS_PROJECT_NAME
export OS_PROJECT_ID="$_ans_project"
export OS_REGION_NAME="$_ans_pool"
export OS_INTERFACE=public
export OS_CLOUD_NAME=selectel
unset _ans_stack _ans_project _ans_pool
