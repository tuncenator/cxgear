#!/usr/bin/env bash
set -euo pipefail
cxgear_source="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing "$cxgear_source/.venv"
  uv pip install --python "$cxgear_source/.venv/bin/python" -e "$cxgear_source"
else
  python3 -m venv "$cxgear_source/.venv"
  "$cxgear_source/.venv/bin/python" -m pip install -e "$cxgear_source"
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$cxgear_source/.venv/bin/cxgear" "$HOME/.local/bin/cxgear"
printf 'cxgear installed. Add accounts, then run: cxgear install\n'
