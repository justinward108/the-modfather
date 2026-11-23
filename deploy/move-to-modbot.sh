#!/usr/bin/env bash
# One-time fix for servers installed before the bot had its own account.
# Moves the bot service from the "modfather" account to a separate "modbot" account.
# It does NOT change the "modfather" user itself. Run as root:  bash deploy/move-to-modbot.sh
set -euo pipefail
id modbot >/dev/null 2>&1 || useradd --system --home /opt/modfather --shell /usr/sbin/nologin modbot
systemctl stop modfather 2>/dev/null || true
chown -R modbot:modbot /opt/modfather
sed -i 's/^User=.*/User=modbot/' /etc/systemd/system/modfather.service
systemctl daemon-reload
systemctl start modfather
echo "The bot now runs as 'modbot'. Your 'modfather' login is untouched."
