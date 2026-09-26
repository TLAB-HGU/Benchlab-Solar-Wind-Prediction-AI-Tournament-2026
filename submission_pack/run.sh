#!/usr/bin/env bash
set -euo pipefail

T0="${1:?FATAL: no timestamp argument supplied by platform}"
echo "[run] t0=${T0} submission=${SUBMISSION_ID:-unknown}"

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT
OUT="$RUN_DIR/forecast.json"

python3 "$APP_DIR/ace_data.py" inference --t0 "$T0" --out "$RUN_DIR/data"
python3 "$APP_DIR/model.py" "$T0" --input "$RUN_DIR/data/input.json" > "$OUT"

echo "[run] produced $(wc -c < "$OUT") bytes"
bash "$APP_DIR/upload.sh" "$OUT"
echo "[run] done"
