#!/usr/bin/env bash
# Полный пайплайн на hh + обновление цифр в отчёте.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
PY="${PYTHON:-.venv/bin/python}"

echo "=== wait other split/extract if any ==="
while pgrep -f "src.market.split_sections" >/dev/null 2>&1; do sleep 20; done
while pgrep -f "src.market.extract_skills_llm" >/dev/null 2>&1; do sleep 20; done

echo "=== 2. Section Splitter (resume) ==="
"$PY" -m src.market.split_sections --source hh --resume

echo "=== 2b. LLM на fallback_full ==="
"$PY" -m src.market.split_sections --source hh --only-fallback --llm-fallback --provider ollama

echo "=== 3. Skill Extractor (from sections, resume) ==="
"$PY" -m src.market.extract_skills_llm --provider ollama --from-sections --require-sections --resume

echo "=== 4-7. Graph + governance ==="
"$PY" -m src.graph.build_market_graph --source hh --skip-org

echo "=== update report metrics ==="
"$PY" scripts/update_report_metrics.py

echo "=== PIPELINE DONE ==="
