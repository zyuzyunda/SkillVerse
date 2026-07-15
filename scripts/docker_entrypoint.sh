#!/usr/bin/env bash
set -euo pipefail

echo "==> init schema"
python -m src.db.init_db

if [[ "${SEED_ON_START:-1}" == "1" ]]; then
  CSV_PATH="${SEED_CSV_PATH:-/data/final_vacancies.csv}"
  if [[ -f "$CSV_PATH" ]]; then
    echo "==> seed from $CSV_PATH"
    python -m src.market.seed_from_csv --csv "$CSV_PATH"
  else
    echo "WARN: seed CSV not found at $CSV_PATH — skip"
  fi
fi

echo "==> done"
