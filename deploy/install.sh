#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 22.04/24.04 Linode. Run as root from the unzipped project folder:
#   sudo bash deploy/install.sh mafia.poor.camp
set -euo pipefail
DOMAIN="${1:?usage: install.sh your.domain}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update
apt-get install -y python3 python3-venv debian-keyring debian-archive-keyring apt-transport-https curl gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi

id modfather >/dev/null 2>&1 || useradd --system --home /opt/modfather --shell /usr/sbin/nologin modfather
mkdir -p /opt/modfather/app /opt/modfather/data
cp -r "$SRC"/modfather "$SRC"/requirements.txt /opt/modfather/app/
[ -f /opt/modfather/app/.env ] || cp "$SRC"/.env.example /opt/modfather/app/.env
python3 -m venv /opt/modfather/venv
/opt/modfather/venv/bin/pip install -q -r /opt/modfather/app/requirements.txt
chown -R modfather:modfather /opt/modfather
chmod 600 /opt/modfather/app/.env

sed "s/mafia.poor.camp/$DOMAIN/" "$SRC"/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
cp "$SRC"/deploy/modfather.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable modfather

if command -v ufw >/dev/null; then ufw allow OpenSSH; ufw allow 80; ufw allow 443; fi

echo
echo "Installed. Next:"
echo "  1. sudo nano /opt/modfather/app/.env   (token, client id/secret, PUBLIC_URL=https://$DOMAIN, SESSION_SECRET)"
echo "  2. sudo systemctl start modfather && sudo journalctl -u modfather -f"
