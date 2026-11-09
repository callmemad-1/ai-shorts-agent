#!/usr/bin/env bash
# Double-click: run the agent from your phone. Keep this window open; it also keeps the Mac awake.
cd "$(dirname "$0")"
caffeinate -i .venv/bin/python -m agent.telegram_bot
read -r -p "Press Enter to close."
