#!/usr/bin/env bash
# Double-click: research today's AI news, fact-check it, then pick which Shorts to make.
cd "$(dirname "$0")"
.venv/bin/python main.py --interactive
read -r -p "Press Enter to close."
