#!/usr/bin/env bash
# Builds "Calendar Agent.app" (macOS) with PyInstaller. Run it on a Mac, from anywhere:
#   ./scripts/build_app.sh
# Result: dist/Calendar Agent.app  (drag it to /Applications)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script builds a macOS app and must run on a Mac." >&2
  echo "On other systems: pip install -e '.[all]' && calendar-agent app" >&2
  exit 1
fi

python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e ".[all,bundle]"

# .icns icon from assets/icon.png (sips and iconutil ship with macOS)
rm -rf build/icon.iconset && mkdir -p build/icon.iconset
for size in 16 32 128 256 512; do
  sips -z $size $size assets/icon.png --out "build/icon.iconset/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) assets/icon.png --out "build/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/icon.iconset -o build/icon.icns

pyinstaller --noconfirm --clean --windowed \
  --name "Calendar Agent" \
  --icon build/icon.icns \
  --osx-bundle-identifier io.github.calendar-agent \
  --paths src \
  --add-data "src/calendar_agent/static:calendar_agent/static" \
  --collect-all googleapiclient --collect-all google_auth_oauthlib --collect-all google.genai \
  --collect-all ollama --collect-all ddgs --collect-all webview \
  scripts/app_entry.py

cat <<MSG

Built: dist/Calendar Agent.app
First run: put your settings next to the app's data, in
  ~/Library/Application Support/Calendar Agent/
    .env              (GEMINI_API_KEY=..., MAIL_BACKEND=gmail, ...)
    credentials.json  (Google OAuth client, only for Google Calendar / Gmail)
Then sign in to Google once with:  .build-venv/bin/calendar-agent auth   (with DATA_DIR set to that folder)
Unsigned apps need right-click -> Open the first time.
MSG
