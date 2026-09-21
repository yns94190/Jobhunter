#!/usr/bin/env bash
# Envoie profile.yaml (non versionne) sur le serveur et recharge l'application.
# Pour le code, un simple "git push" suffit : la pipeline GitHub Actions deploie.
set -euo pipefail

SERVER=$(grep -E '^DEPLOY_SERVER=' .env | cut -d= -f2-)
URL=$(grep -E '^DEPLOY_URL=' .env | cut -d= -f2-)
: "${SERVER:?DEPLOY_SERVER manquant dans .env}"

echo "-> Envoi du profil"
scp -q profile.yaml "$SERVER":~/jobhunter/profile.yaml

echo "-> Rechargement de l'application"
ssh "$SERVER" "cd ~/jobhunter && docker compose restart api >/dev/null"

sleep 12
curl -fsS "$URL" && echo && echo "Profil synchronise"
