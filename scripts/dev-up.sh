#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo ".env not found - copying .env.example. Edit it (JWT_SECRET_KEY, SECRET_ENCRYPTION_KEY) before real use." >&2
  cp .env.example .env
fi

docker compose up --build
