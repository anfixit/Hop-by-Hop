#!/usr/bin/env bash
# Первичная настройка чистого Ubuntu 24.04 под бота. Запускается один раз от root:
#   ssh hbh 'bash -s' < deploy/bootstrap.sh "<публичный ключ деплоя>"
# Скрипт идемпотентен: повторный запуск ничего не ломает.
set -euo pipefail

DEPLOY_PUBKEY="${1:?нужен публичный ключ деплоя первым аргументом}"
APP_DIR=/opt/hopbyhop

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ufw unattended-upgrades fail2ban
# Docker: если уже стоит (например, docker-ce из репозитория Docker), не трогаем;
# иначе ставим из репозитория Ubuntu - пакеты подписаны и обновляются вместе с системой
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get install -yq docker.io docker-compose-v2
fi

# Автоматические обновления безопасности
dpkg-reconfigure -f noninteractive unattended-upgrades

# Пользователь для деплоя: только ключ, без пароля.
# Внимание: группа docker даёт права, эквивалентные root. Это компромисс ради простоты;
# ключ деплоя хранится только в секретах GitHub.
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
passwd -l deploy >/dev/null
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf '%s\n' "$DEPLOY_PUBKEY" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

install -d -m 750 -o deploy -g deploy "$APP_DIR"

# SSH: только по ключам
cat > /etc/ssh/sshd_config.d/10-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 3
X11Forwarding no
EOF
sshd -t
systemctl reload ssh

# Файрвол: боту входящие соединения не нужны (он сам ходит в Telegram), открыт только SSH
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# Стандартный фильтр fail2ban отбирает записи по _SYSTEMD_UNIT=sshd.service ИЛИ _COMM=sshd.
# В Ubuntu 24.04 служба называется ssh.service (сокет-активация), так что первая половина
# условия не срабатывает и всё держится на имени процесса. Указываем имя службы явно.
cat > /etc/fail2ban/jail.d/sshd-ubuntu.local <<'EOF'
[sshd]
enabled = true
backend = systemd
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
EOF

systemctl enable --now fail2ban docker
fail2ban-client reload >/dev/null

echo "--- готово"
docker --version
docker compose version
ufw status verbose | head -n 8
