#!/usr/bin/env bash
# finderX — one-command start: makes a virtualenv on first run, then serves the terminal.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "  creating virtualenv (.venv) …"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

# First run: load the candidates from the first-light survey so the queue isn't empty.
if [ ! -f "${FINDERX_DATA:-data}/finderx.db" ] && [ -f docs/first-light.json.gz ]; then
  .venv/bin/python -m finderx import docs/first-light.json.gz
fi

exec .venv/bin/python -m finderx serve --open "$@"
