#!/usr/bin/env bash
# One-time setup for AI Shorts Agent on macOS.
#  - installs Homebrew (if missing), ffmpeg and Python 3
#  - creates a virtualenv and installs the Python packages
#  - asks for your Anthropic API key (optional, stored only in .env here)
#  - renders the example Short as a test
#  - optionally schedules a daily run with launchd
set -e
cd "$(dirname "$0")"
PROJECT="$(pwd)"
bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }

bold "1/5  Checking Homebrew, ffmpeg and Python"
if ! command -v brew >/dev/null 2>&1; then
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$p" ] && eval "$($p shellenv)"; done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew (it may ask for your Mac password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
command -v ffmpeg >/dev/null 2>&1 || brew install ffmpeg
PY=""
for c in python3.13 python3.12 python3.11 python3.10; do
  if command -v $c >/dev/null 2>&1; then PY=$c; break; fi
done
if [ -z "$PY" ]; then brew install python@3.12; PY=python3.12; fi
echo "Using $($PY --version), $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"

bold "2/5  Creating virtualenv and installing packages"
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "Packages installed."

bold "3/5  Anthropic API key (needed for automatic research + fact-check)"
if [ -f .env ] && grep -q "sk-ant-" .env; then
  echo "API key already saved in .env"
else
  echo "Get one at https://console.anthropic.com (Settings > API Keys)."
  echo "Paste it now and press Enter, or just press Enter to skip for now."
  read -r -s -p "API key: " KEY; echo
  if [ -n "$KEY" ]; then
    printf "ANTHROPIC_API_KEY=%s\n" "$KEY" > .env
    chmod 600 .env
    echo "Saved to .env (only readable by you)."
  else
    cp -n .env.example .env 2>/dev/null || true
    echo "Skipped. You can add it later in $PROJECT/.env"
  fi
fi

bold "4/5  Test render (example Short from Nov 5)"
if ! python main.py --from-script examples/linux-948-bugs.script.json --out outputs/setup-test; then
  echo "Online voice unavailable; downloading the free offline voice (~350 MB) instead..."
  python setup_offline_voice.py
  python main.py --from-script examples/linux-948-bugs.script.json --out outputs/setup-test --tts kokoro
fi
echo "Test video: $PROJECT/outputs/setup-test/short.mp4"
open outputs/setup-test/short.mp4 2>/dev/null || true

bold "5/5  Daily automatic run"
PLIST="$HOME/Library/LaunchAgents/com.aishorts.daily.plist"
if grep -q "sk-ant-" .env 2>/dev/null; then
  read -r -p "Make a new Short automatically every day at 7:45 AM? [Y/n] " ans
  if [[ ! "$ans" =~ ^[Nn] ]]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.aishorts.daily</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$PROJECT/run_daily.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>45</integer></dict>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$PROJECT/outputs/daily.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/outputs/daily.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Scheduled. Each day's Short goes to $PROJECT/outputs/<date>/<topic>/short.mp4 (your Mac must be awake)."
    echo "To stop it: launchctl unload $PLIST"
  fi
else
  echo "No API key yet, so no daily automatic run was scheduled."
  echo "You can still render scripts from your Claude project or the daily Claude task:"
  echo "  cd \"$PROJECT\" && source .venv/bin/activate && python main.py --from-script path/to/script.json"
fi

bold "Setup complete."
echo "Run a full Short any time:   cd \"$PROJECT\" && ./run_daily.sh"
