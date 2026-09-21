#!/usr/bin/env bash
# Execute SUR LE SERVEUR par la pipeline GitHub Actions.
# Tout est dans une fonction : bash lit le script en entier avant de l'executer,
# ce qui evite les comportements etranges quand git met a jour ce fichier en cours de route.
set -euo pipefail

main() {
    cd ~/jobhunter
    echo "-> Recuperation de la derniere version"
    git fetch -q origin main
    git reset -q --hard origin/main
    echo "-> Reconstruction et redemarrage"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans 2>&1 | tail -3
    echo "-> Migrations de base de donnees"
    docker compose exec -T api alembic upgrade head
    echo "-> Version deployee : $(git log -1 --format='%h %s')"
}

main "$@"
exit 0
