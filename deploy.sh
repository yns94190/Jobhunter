#!/usr/bin/env bash
# Publie la derniere version de main sur le serveur de production.
set -euo pipefail

SERVER="ubuntu@141.253.98.8"
URL="https://141-253-98-8.sslip.io/health"

echo "-> Envoi du code sur GitHub"
git push -q

echo "-> Mise a jour du serveur"
ssh "$SERVER" "cd ~/jobhunter && git pull -q && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build 2>&1 | tail -2 && docker compose exec -T api alembic upgrade head"

echo "-> Verification"
sleep 15
curl -fsS "$URL" && echo && echo "Deploiement OK"
