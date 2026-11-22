#!/usr/bin/env bash
# Launch the read-only AI Migration Studio demo from the real project repo.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
if [[ ! -f "manifests/program0.json" || ! -f "manifests/program1.json" || ! -f "manifests/program2.json" ]]; then
  echo >&2 "ERROR: This demo is not inside your SAS migration Git repository."
  echo >&2 "Unzip the archive into: $HOME/Consulting-1D-ai-powered-sas-migration"
  echo >&2 "Then run: bash demo/run_migration_studio_final_v11_2.sh"
  exit 1
fi
if [[ ! -d "output/chain_runs" ]]; then
  echo >&2 "WARNING: output/chain_runs is not present. The app will show preview values only."
fi
PYTHON_BIN="${PYTHON_BIN:-$HOME/sas-migration/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN=python3; fi
exec "$PYTHON_BIN" -m streamlit run demo/migration_studio.py \
  --server.port=8519 \
  --theme.base=light \
  --theme.primaryColor='#245FCF' \
  --theme.backgroundColor='#F1F5FB' \
  --theme.secondaryBackgroundColor='#FFFFFF' \
  --theme.textColor='#202A3A' \
  --theme.codeBackgroundColor='#FFFFFF' \
  --theme.codeTextColor='#202A3A' \
  --theme.dataframeHeaderBackgroundColor='#F5F8FC'
