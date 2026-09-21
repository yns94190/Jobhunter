#!/usr/bin/env bash
# Publie la derniere version de main sur le serveur de production.
set -euo pipefail

SERVER=$(grep -E '^DEPLOY_SERVER=' .env | cut -d= -f2-)
URL=$(grep -E '^DEPLOY_URL=' .env | cut -d= -f2-)
: "${SERVER:?DEPLOY_SERVER manquant dans .env}"
: "${URL:?DEPLOY_URL manquant dans .env}"

echo "-> Envoi du code sur GitHub"
git push -q

echo "-> Envoi du profil (non versionne)"
scp -q profile.yaml "$SERVER":~/jobhunter/profile.yaml

echo "-> Mise a jour du serveur"
ssh "$SERVER" "cd ~/jobhunter && git pull -q && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --force-recreate 2>&1 | tail -2 && docker compose exec -T api alembic upgrade head"

echo "-> Verification"
sleep 15
curl -fsS "$URL" && echo && echo "Deploiement OK"
