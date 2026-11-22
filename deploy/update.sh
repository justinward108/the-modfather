#!/usr/bin/env bash
# Deploy a new version: in your clone run  git pull  then  sudo bash deploy/update.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
cp /opt/modfather/data/modfather.db "/opt/modfather/data/modfather.db.bak-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
rm -rf /opt/modfather/app/modfather
cp -r "$SRC"/modfather "$SRC"/requirements.txt /opt/modfather/app/
/opt/modfather/venv/bin/pip install -q -r /opt/modfather/app/requirements.txt
chown -R modfather:modfather /opt/modfather
systemctl restart modfather
echo "Updated. Running games resume where they left off."
