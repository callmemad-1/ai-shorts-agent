#!/usr/bin/env bash
# Daily run for cron. Uses the project's virtualenv if present.
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
python main.py "$@"
