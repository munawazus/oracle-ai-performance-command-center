#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"

python -m ruff check app tests --line-length 110
python -m mypy app --ignore-missing-imports
python -m bandit -q -r app
python -m pytest -q
